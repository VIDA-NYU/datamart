import contextlib
import elasticsearch
import logging
import json
import prometheus_client

from datamart_core.materialize import get_dataset
from datamart_core.objectstore import get_object_store
from datamart_core.prom import PromMeasureRequest

from .base import BUCKETS, BaseHandler
from .enhance_metadata import enhance_metadata
from .graceful_shutdown import GracefulHandler
from .profile import ProfilePostedData


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

        # If it's in object storage
        if (
            not session_id
            and format == 'csv'
            and not materialize.get('convert')
        ):
            object_store = get_object_store()
            with contextlib.ExitStack() as stack:
                try:
                    dataset = stack.enter_context(
                        object_store.open('datasets', dataset_id)
                    )
                except FileNotFoundError:
                    pass
                else:
                    return self.redirect(object_store.file_url(dataset))

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
        if type_.startswith('application/json'):
            task = self.get_json()
        elif (type_.startswith('multipart/form-data') or
                type_.startswith('application/x-www-form-urlencoded')):
            task = self.get_body_argument('task', None)
            if task is None and 'task' in self.request.files:
                task = self.request.files['task'][0].body.decode('utf-8')
            if task is not None:
                task = json.loads(task)
            if 'format' in self.request.files:
                return await self.send_error_json(
                    400,
                    "Sending 'format' in the POST data is no longer "
                    "supported, please use query parameters",
                )
        if task is None:
            return await self.send_error_json(
                400,
                "Either use multipart/form-data to send a 'task' JSON file, "
                "or use application/json",
            )

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

        return await self.send_dataset(
            task.get('id', 'unknown_id'),
            metadata,
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
                # Don't expose the details of the problem (e.g. stacktrace)
                record.pop('error_details')
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
