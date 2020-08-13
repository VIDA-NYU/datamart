import contextlib
import elasticsearch
import io
import logging
import json
import os
import prometheus_client
import shutil
import tempfile
import zipfile

from datamart_core.augment import augment
from datamart_core.materialize import get_dataset, make_zip_recursive
from datamart_core.prom import PromMeasureRequest
from datamart_materialize import make_writer

from .base import BUCKETS, BaseHandler
from .enhance_metadata import enhance_metadata
from .graceful_shutdown import GracefulHandler
from .profile import ProfilePostedData
from .search import get_augmentation_search_results


logger = logging.getLogger(__name__)


PROM_DOWNLOAD = PromMeasureRequest(
    count=prometheus_client.Counter(
        'req_download_count',
        "Download requests",
    ),
    time=prometheus_client.Histogram(
        'req_download_seconds',
        "Download request time",
        buckets=BUCKETS,
    ),
)
PROM_METADATA = PromMeasureRequest(
    count=prometheus_client.Counter(
        'req_metadata_count',
        "Metadata requests",
    ),
    time=prometheus_client.Histogram(
        'req_metadata_seconds',
        "Metadata request time",
        buckets=BUCKETS,
    ),
)


class BaseDownload(BaseHandler):
    async def send_dataset(self, dataset_id, metadata):
        format, format_options, format_ext = self.read_format()

        materialize = metadata.get('materialize', {})

        session_id = self.get_query_argument('session_id', None)

        # If there's a direct download URL
        if (
            'direct_url' in materialize
            and not session_id
            and format == 'csv' and not materialize.get('convert')
        ):
            if format_options:
                return await self.send_error_json(
                    400,
                    "Invalid output options",
                )
            # Redirect the client to it
            logger.info("Sending redirect to direct_url")
            return self.redirect(materialize['direct_url'])

        with contextlib.ExitStack() as stack:
            try:
                dataset_path = stack.enter_context(
                    get_dataset(
                        metadata, dataset_id,
                        format=format, format_options=format_options,
                    )
                )
            except Exception:
                await self.send_error_json(500, "Materializer reports failure")
                raise

            if session_id:
                logger.info("Attaching to session")
                self.application.redis.rpush(
                    'session:' + session_id,
                    json.dumps(
                        {
                            'type': 'download',
                            'url': (
                                '/download/' + dataset_id + '?'
                                + self.serialize_format(format, format_options)
                            ),
                        },
                        # Compact
                        sort_keys=True, indent=None, separators=(',', ':'),
                    ),
                )
                return await self.send_json({'success': "attached to session"})
            else:
                logger.info("Sending file...")
                return await self.send_file(
                    dataset_path,
                    dataset_id + (format_ext or ''),
                )


class DownloadId(BaseDownload, GracefulHandler):
    @PROM_DOWNLOAD.sync()
    def get(self, dataset_id):
        # Get materialization data from Elasticsearch
        try:
            metadata = self.application.elasticsearch.get(
                'datamart', dataset_id
            )['_source']
        except elasticsearch.NotFoundError:
            return self.send_error_json(404, "No such dataset")

        return self.send_dataset(dataset_id, metadata)


class Download(BaseDownload, GracefulHandler, ProfilePostedData):
    @PROM_DOWNLOAD.sync()
    async def post(self):
        type_ = self.request.headers.get('Content-type', '')

        task = None
        data = None
        if type_.startswith('application/json'):
            task = self.get_json()
        elif (type_.startswith('multipart/form-data') or
                type_.startswith('application/x-www-form-urlencoded')):
            task = self.get_body_argument('task', None)
            if task is None and 'task' in self.request.files:
                task = self.request.files['task'][0].body.decode('utf-8')
            if task is not None:
                task = json.loads(task)
            data = self.get_body_argument('data', None)
            if 'data' in self.request.files:
                data = self.request.files['data'][0].body
            elif data is not None:
                data = data.encode('utf-8')
            if 'format' in self.request.files:
                return await self.send_error_json(
                    400,
                    "Sending 'format' in the POST data is no longer "
                    "supported, please use query parameters",
                )
        if task is None:
            return await self.send_error_json(
                400,
                "Either use multipart/form-data to send the 'data' file and "
                "'task' JSON, or use application/json to send 'task' alone",
            )

        logger.info("Got POST download %s data",
                    "without" if data is None else "with")

        if 'metadata' in task:
            metadata = task['metadata']
        elif 'id' in task:
            # Get materialization data from Elasticsearch
            try:
                metadata = self.application.elasticsearch.get(
                    'datamart', task['id']
                )['_source']
            except elasticsearch.NotFoundError:
                return await self.send_error_json(404, "No such dataset")
        else:
            return await self.send_error_json(
                400,
                "No metadata or ID specified",
            )

        if not data:
            return await self.send_dataset(task['id'], metadata)
        else:
            format, format_options, format_ext = self.read_format()

            # data
            data_profile, _ = self.handle_data_parameter(data)

            # first, look for possible augmentation
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

            if not search_results:
                return await self.send_error_json(
                    400,
                    "The Datamart dataset referenced by 'task' cannot augment "
                    "'data'",
                )

            task = search_results[0]

            with tempfile.TemporaryDirectory(prefix='datamart_aug_') as tmp:
                new_path = os.path.join(tmp, 'dataset')
                with get_dataset(metadata, task['id'], format='csv') as newdata:
                    # perform augmentation
                    logger.info("Performing half-augmentation with supplied data")
                    writer = make_writer(new_path, format, format_options)
                    augment(
                        io.BytesIO(data),
                        newdata,
                        data_profile,
                        task,
                        writer,
                        return_only_datamart_data=True
                    )

                    # ZIP result if it's a directory
                    if os.path.isdir(new_path):
                        logger.info("Result is a directory, creating ZIP file")
                        zip_name = new_path + '.zip'
                        with zipfile.ZipFile(zip_name, 'w') as zip_:
                            make_zip_recursive(zip_, new_path)
                        shutil.rmtree(new_path)
                        os.rename(zip_name, new_path)

            return await self.send_file(
                new_path,
                name='augmentation' + (format_ext or ''),
            )


class Metadata(BaseHandler, GracefulHandler):
    @PROM_METADATA.sync()
    def get(self, dataset_id):
        es = self.application.elasticsearch
        try:
            metadata = es.get('datamart', dataset_id)['_source']
        except elasticsearch.NotFoundError:
            # Check alternate index
            try:
                record = es.get('pending', dataset_id)['_source']
            except elasticsearch.NotFoundError:
                return self.send_error_json(404, "No such dataset")
            else:
                result = {
                    'id': dataset_id,
                    'status': record['status'],
                    'metadata': record['metadata'],
                }
                if 'error' in record:
                    result['error'] = record['error']
        else:
            result = {
                'id': dataset_id,
                'status': 'indexed',
                'metadata': metadata,
            }
            result = enhance_metadata(result)

        return self.send_json(result)

    head = get
