import contextlib
import io
import logging
import json
import os
import prometheus_client
import shutil
import zipfile

from datamart_augmentation.augmentation import AugmentationError
from datamart_core.augment import augment
from datamart_core.common import hash_json, contextdecorator
from datamart_core.fscache import cache_get, cache_get_or_set
from datamart_core.materialize import get_dataset, make_zip_recursive
from datamart_core.prom import PromMeasureRequest
from datamart_materialize import make_writer

from .base import BUCKETS, BaseHandler
from .graceful_shutdown import GracefulHandler
from .profile import ProfilePostedData, get_data_profile_from_es, \
    profile_token_re
from .search import get_augmentation_search_results


logger = logging.getLogger(__name__)


PROM_AUGMENT = PromMeasureRequest(
    count=prometheus_client.Counter(
        'req_augment_count',
        "Augment requests",
    ),
    time=prometheus_client.Histogram(
        'req_augment_seconds',
        "Augment request time",
        buckets=BUCKETS,
    ),
)
PROM_AUGMENT_RESULT = PromMeasureRequest(
    count=prometheus_client.Counter(
        'req_augment_result_count',
        "Augment result requests",
    ),
    time=prometheus_client.Histogram(
        'req_augment_result_seconds',
        "Augment result request time",
        buckets=BUCKETS,
    ),
)


class Augment(BaseHandler, GracefulHandler, ProfilePostedData):
    @PROM_AUGMENT.sync()
    @contextdecorator(contextlib.ExitStack, 'stack')
    async def post(self, stack):
        format, format_options, format_ext = self.read_format('d3m')

        session_id = self.get_query_argument('session_id', None)

        type_ = self.request.headers.get('Content-type', '')
        if not type_.startswith('multipart/form-data'):
            return await self.send_error_json(
                400,
                "Use multipart/form-data to send the 'data' file and "
                "'task' JSON",
            )

        task = self.get_body_argument('task', None)
        if task is None and 'task' in self.request.files:
            task = self.request.files['task'][0].body.decode('utf-8')
        if task is None:
            return await self.send_error_json(400, "Missing 'task' JSON")
        task = json.loads(task)

        data = self.get_body_argument('data', None)
        if data is not None:
            data = data.encode('utf-8')
        elif 'data' in self.request.files:
            data = self.request.files['data'][0].body

        data_id = self.get_body_argument('data_id', None)
        if 'data_id' in self.request.files:
            data_id = self.request.files['data_id'][0].body.decode('utf-8')

        columns = self.get_body_argument('columns', None)
        if 'columns' in self.request.files:
            columns = self.request.files['columns'][0].body.decode('utf-8')
        if columns is not None:
            columns = json.loads(columns)

        logger.info("Got augmentation, content-type=%r", type_.split(';')[0])

        # data
        if data_id is not None and data is not None:
            return await self.send_error_json(
                400,
                "Please only provide one input dataset " +
                "(either 'data' or 'data_id')",
            )
        elif data_id is not None:
            data_profile = get_data_profile_from_es(
                self.application.elasticsearch,
                data_id,
            )
            data_hash = None
            if data_profile is None:
                return await self.send_error_json(400, "No such dataset")
        elif data is not None:
            if len(data) == 40:
                try:
                    data_token = data.decode('ascii')
                except UnicodeDecodeError:
                    pass
                else:
                    if profile_token_re.match(data_token):
                        data = stack.enter_context(cache_get(
                            '/cache/user_data',
                            data_token,
                        ))
                        if data is None:
                            return self.send_error_json(
                                404,
                                "Data token expired",
                            )
                        else:
                            with open(data, 'rb') as fp:
                                data = fp.read()
            data_profile, data_hash = self.handle_data_parameter(data)
        else:
            return await self.send_error_json(400, "Missing 'data'")

        # materialize augmentation data
        metadata = task['metadata']

        # no augmentation task provided -- will first look for possible augmentation
        if 'augmentation' not in task or task['augmentation']['type'] == 'none':
            logger.info("No task, searching for augmentations")
            search_results = get_augmentation_search_results(
                es=self.application.elasticsearch,
                lazo_client=self.application.lazo_client,
                data_profile=data_profile,
                query_args_main=None,
                query_sup_functions=None,
                query_sup_filters=None,
                tabular_variables=None,
                dataset_id=task['id'],
                union=False
            )

            if search_results:
                # get first result
                task = search_results[0]
                logger.info("Using first of %d augmentation results: %r",
                            len(search_results), task['id'])
            else:
                return await self.send_error_json(
                    400,
                    "The Datamart dataset referenced by 'task' cannot "
                    "augment 'data'",
                )

        key = hash_json(
            task=task,
            supplied_data=data_hash or data_id,
            columns=columns,
            format=format,
            format_options=format_options,
        )

        def create_aug(cache_temp):
            with contextlib.ExitStack() as stack:
                # Get augmentation data
                newdata = stack.enter_context(
                    get_dataset(metadata, task['id'], format='csv'),
                )
                # Get input data if it's a reference to a dataset
                if data_id:
                    path = stack.enter_context(
                        get_dataset(data_profile, data_id, format='csv'),
                    )
                    data_file = stack.enter_context(open(path, 'rb'))
                else:
                    data_file = io.BytesIO(data)
                # Perform augmentation
                writer = make_writer(cache_temp, format, format_options)
                logger.info("Performing augmentation with supplied data")
                augment(
                    data_file,
                    newdata,
                    data_profile,
                    task,
                    writer,
                    columns=columns,
                )

                # ZIP result if it's a directory
                if os.path.isdir(cache_temp):
                    logger.info("Result is a directory, creating ZIP file")
                    zip_name = cache_temp + '.zip'
                    with zipfile.ZipFile(zip_name, 'w') as zip_:
                        make_zip_recursive(zip_, cache_temp)
                    shutil.rmtree(cache_temp)
                    os.rename(zip_name, cache_temp)

        try:
            with cache_get_or_set('/cache/aug', key, create_aug) as path:
                if session_id:
                    self.application.redis.rpush(
                        'session:' + session_id,
                        json.dumps(
                            {
                                'type': task['augmentation']['type'],
                                'url': '/augment/' + key,
                            },
                            # Compact
                            sort_keys=True, indent=None, separators=(',', ':'),
                        )
                    )
                    return await self.send_json({
                        'success': "attached to session",
                    })
                else:
                    # send the file
                    return await self.send_file(
                        path,
                        name='augmentation' + (format_ext or ''),
                    )
        except AugmentationError as e:
            return await self.send_error_json(400, str(e))


class AugmentResult(BaseHandler):
    @PROM_AUGMENT_RESULT.sync()
    async def get(self, key):
        with cache_get('/cache/aug', key) as path:
            if path:
                return await self.send_file(
                    path,
                    name='augmentation',
                )
            else:
                return self.send_error_json(404, "Data not in cache")
