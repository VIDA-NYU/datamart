import aio_pika
import asyncio
import contextlib
import csv
import elasticsearch
import io
import lazo_index_service
import logging
import json
import os
import prometheus_client
import redis
import shutil
import tornado.ioloop
from tornado.routing import URLSpec
import tornado.httputil
import tornado.web
from tornado.web import HTTPError, RequestHandler
import zipfile

from datamart_augmentation.augmentation import AugmentationError, augment
from datamart_core.common import setup_logging, hash_json, log_future
from datamart_core.fscache import cache_get_or_set
from datamart_core.materialize import get_dataset
import datamart_profiler

from .enhance_metadata import enhance_metadata
from .graceful_shutdown import GracefulApplication, GracefulHandler
from .search import TOP_K_SIZE, ClientError, parse_query, \
    get_augmentation_search_results, ProfilePostedData


logger = logging.getLogger(__name__)


BUCKETS = [0.5, 1.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0, 300.0, 600.0]

PROM_PROFILE_TIME = prometheus_client.Histogram('req_profile_seconds',
                                                "Profile request time",
                                                buckets=BUCKETS)
PROM_PROFILE = prometheus_client.Counter('req_profile_count',
                                         "Profile requests")
PROM_SEARCH_TIME = prometheus_client.Histogram('req_search_seconds',
                                               "Search request time",
                                               buckets=BUCKETS)
PROM_SEARCH = prometheus_client.Counter('req_search_count',
                                        "Search requests")
PROM_DOWNLOAD_TIME = prometheus_client.Histogram('req_download_seconds',
                                                 "Download request time",
                                                 buckets=BUCKETS)
PROM_DOWNLOAD = prometheus_client.Counter('req_download_count',
                                          "Download requests")
PROM_DOWNLOAD_ID = prometheus_client.Counter('req_download_id_count',
                                             "Download by ID requests")
PROM_METADATA_TIME = prometheus_client.Histogram('req_metadata_seconds',
                                                 "Metadata request time",
                                                 buckets=BUCKETS)
PROM_METADATA = prometheus_client.Counter('req_metadata_count',
                                          "Metadata requests")
PROM_AUGMENT_TIME = prometheus_client.Histogram('req_augment_seconds',
                                                "Augment request time",
                                                buckets=BUCKETS)
PROM_AUGMENT = prometheus_client.Counter('req_augment_count',
                                         "Augment requests")


class BaseHandler(RequestHandler):
    """Base class for all request handlers.
    """

    def set_default_headers(self):
        self.set_header('Server', 'Auctus/%s' % os.environ['DATAMART_VERSION'])

    def get_json(self):
        type_ = self.request.headers.get('Content-Type', '')
        if not type_.startswith('application/json'):
            raise HTTPError(400, "Expected JSON")
        return json.loads(self.request.body.decode('utf-8'))

    def send_json(self, obj):
        if isinstance(obj, list):
            obj = {'results': obj}
        elif not isinstance(obj, dict):
            raise ValueError("Can't encode %r to JSON" % type(obj))
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        return self.finish(json.dumps(obj))

    def send_error_json(self, status, message):
        logger.info("Sending error %s JSON: %s", status, message)
        self.set_status(status)
        return self.send_json({'error': message})

    def prepare(self):
        super(BaseHandler, self).prepare()
        self.set_header('Access-Control-Allow-Origin', '*')
        self.set_header('Access-Control-Allow-Methods', 'POST')
        self.set_header('Access-Control-Allow-Headers', 'Content-Type')

    def options(self):
        # CORS pre-flight
        self.set_status(204)
        return self.finish()


class Profile(BaseHandler, GracefulHandler, ProfilePostedData):
    @PROM_PROFILE_TIME.time()
    def post(self):
        PROM_PROFILE.inc()

        data = self.get_body_argument('data', None)
        if 'data' in self.request.files:
            data = self.request.files['data'][0].body
        elif data is not None:
            data = data.encode('utf-8')

        if data is None:
            return self.send_error_json(
                400,
                "Please send 'data' as a file, using multipart/form-data",
            )

        logger.info("Got profile")

        try:
            data_profile, _ = self.handle_data_parameter(data)
        except ClientError as e:
            return self.send_error_json(400, str(e))

        return self.send_json(dict(
            data_profile,
            version=os.environ['DATAMART_VERSION'],
        ))


class Search(BaseHandler, GracefulHandler, ProfilePostedData):
    @PROM_SEARCH_TIME.time()
    def post(self):
        PROM_SEARCH.inc()

        type_ = self.request.headers.get('Content-type', '')
        data = None
        data_profile = None
        if type_.startswith('application/json'):
            query = self.get_json()
        elif (type_.startswith('multipart/form-data') or
                type_.startswith('application/x-www-form-urlencoded')):
            query = self.get_body_argument('query', None)
            if query is None and 'query' in self.request.files:
                query = self.request.files['query'][0].body.decode('utf-8')
            if query is not None:
                query = json.loads(query)

            data = self.get_body_argument('data', None)
            if 'data' in self.request.files:
                data = self.request.files['data'][0].body
            elif data is not None:
                data = data.encode('utf-8')

            data_profile = self.get_body_argument('data_profile', None)
            if data_profile is None and 'data_profile' in self.request.files:
                data_profile = self.request.files['data_profile'][0].body
                data_profile = data_profile.decode('utf-8')
            if data_profile is not None:
                data_profile = json.loads(data_profile)

        elif (type_.startswith('text/csv') or
                type_.startswith('application/csv')):
            query = None
            data = self.request.body
        else:
            return self.send_error_json(
                400,
                "Either use multipart/form-data to send the 'query' JSON and "
                "'data' file (or 'data_profile' JSON), or use "
                "application/json to send a query alone, or use text/csv to "
                "send data alone",
            )

        if data is not None and data_profile is not None:
            return self.send_error_json(
                400,
                "Please send either 'data' or 'data_profile'",
            )

        logger.info("Got search, content-type=%r%s%s%s",
                    type_.split(';')[0],
                    ', query' if query else '',
                    ', data' if data else '',
                    ', data_profile' if data_profile else '')

        # parameter: data
        if data:
            try:
                data_profile, _ = self.handle_data_parameter(data)
            except ClientError as e:
                return self.send_error_json(400, str(e))

        # parameter: query
        query_args_main = list()
        query_args_sup = list()
        tabular_variables = list()
        if query:
            try:
                query_args_main, query_args_sup, tabular_variables = \
                    parse_query(query)
            except ClientError as e:
                return self.send_error_json(400, str(e))

        # At least one of them must be provided
        if not query_args_main and not data_profile:
            return self.send_error_json(
                400,
                "At least one of 'data' or 'query' must be provided",
            )

        if not data_profile:
            hits = self.application.elasticsearch.search(
                index='datamart',
                body={
                    'query': {
                        'bool': {
                            'must': query_args_main,
                        },
                    },
                },
                size=TOP_K_SIZE,
            )['hits']['hits']

            results = []
            for h in hits:
                meta = h.pop('_source')
                if meta.get('description') and len(meta['description']) > 100:
                    meta['description'] = meta['description'][:97] + "..."
                results.append(dict(
                    id=h['_id'],
                    score=h['_score'],
                    metadata=meta,
                    augmentation={
                        'type': 'none',
                        'left_columns': [],
                        'left_columns_names': [],
                        'right_columns': [],
                        'right_columns_names': []
                    },
                    supplied_id=None,
                    supplied_resource_id=None
                ))
        else:
            results = get_augmentation_search_results(
                self.application.elasticsearch,
                self.application.lazo_client,
                data_profile,
                query_args_main,
                query_args_sup,
                tabular_variables,
            )
        results = [enhance_metadata(result) for result in results]

        # Private API for the frontend, don't want clients to rely on it
        if self.get_query_argument('_parse_sample', ''):
            for result in results:
                sample = result['metadata'].get('sample', None)
                if sample:
                    result['sample'] = list(csv.reader(io.StringIO(sample)))

        return self.send_json(results)


class RecursiveZipWriter(object):
    def __init__(self, write):
        self._write = write
        self._zip = zipfile.ZipFile(self, 'w')

    def write_recursive(self, src, dst=''):
        if os.path.isdir(src):
            for name in os.listdir(src):
                self.write_recursive(os.path.join(src, name),
                                     dst + '/' + name if dst else name)
        else:
            self._zip.write(src, dst)

    def write(self, data):
        self._write(data)
        return len(data)

    def flush(self):
        return

    def close(self):
        self._zip.close()


class BaseDownload(BaseHandler):
    def read_format(self):
        format = self.get_query_argument('format', 'csv')
        format_options = {}
        for n, v in self.request.query_arguments.items():
            if n.startswith('format_'):
                if len(v) != 1:
                    self.send_error_json(
                        400,
                        "Multiple occurrences of format option %r" % n[7:],
                    )
                    raise HTTPError(400)
                format_options[n[7:]] = self.decode_argument(v[0])
        return format, format_options

    async def send_dataset(self, dataset_id, metadata,
                           format='csv', format_options=None):
        materialize = metadata.get('materialize', {})

        # If there's a direct download URL
        if ('direct_url' in materialize and
                format == 'csv' and not materialize.get('convert')):
            if format_options:
                return self.send_error_json(400, "Invalid output options")
            # Redirect the client to it
            logger.info("Sending redirect to direct_url")
            return self.redirect(materialize['direct_url'])
        else:
            # We want to catch exceptions from get_dataset(), without catching
            # exceptions from inside the with block
            # https://docs.python.org/3/library/contextlib.html#catching-exceptions-from-enter-methods
            stack = contextlib.ExitStack()
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
            with stack:
                if zipfile.is_zipfile(dataset_path):
                    self.set_header('Content-Type', 'application/zip')
                    self.set_header(
                        'Content-Disposition',
                        'attachment; filename="%s.zip"' % dataset_id)
                    logger.info("Sending ZIP...")
                else:
                    self.set_header('Content-Type', 'application/octet-stream')
                    self.set_header('X-Content-Type-Options', 'nosniff')
                    self.set_header('Content-Disposition',
                                    'attachment; filename="%s"' % dataset_id)
                    logger.info("Sending file...")
                with open(dataset_path, 'rb') as fp:
                    BUFSIZE = 40960
                    buf = fp.read(BUFSIZE)
                    while buf:
                        self.write(buf)
                        if len(buf) != BUFSIZE:
                            break
                        buf = fp.read(BUFSIZE)
                    await self.flush()
                return self.finish()


class DownloadId(BaseDownload, GracefulHandler):
    @PROM_DOWNLOAD_TIME.time()
    def get(self, dataset_id):
        PROM_DOWNLOAD_ID.inc()

        format, format_options = self.read_format()

        # Get materialization data from Elasticsearch
        try:
            metadata = self.application.elasticsearch.get(
                'datamart', dataset_id
            )['_source']
        except elasticsearch.NotFoundError:
            raise HTTPError(404)

        return self.send_dataset(dataset_id, metadata, format, format_options)


class Download(BaseDownload, GracefulHandler, ProfilePostedData):
    @PROM_DOWNLOAD_TIME.time()
    def post(self):
        PROM_DOWNLOAD.inc()

        type_ = self.request.headers.get('Content-type', '')

        task = None
        data = None
        format, format_options = self.read_format()
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
                return self.send_error_json(
                    400,
                    "Sending 'format' in the POST data is no longer "
                    "supported, please use query parameters",
                )
        if task is None:
            return self.send_error_json(
                400,
                "Either use multipart/form-data to send the 'data' file and "
                "'task' JSON, or use application/json to send 'task' alone",
            )

        logger.info("Got POST download %s data",
                    "without" if data is None else "with")

        # materialize augmentation data
        metadata = task['metadata']

        if not data:
            return self.send_dataset(
                task['id'], metadata, format, format_options,
            )
        else:
            # data
            try:
                data_profile, _ = self.handle_data_parameter(data)
            except ClientError as e:
                return self.send_error_json(400, str(e))

            # first, look for possible augmentation
            search_results = get_augmentation_search_results(
                es=self.application.elasticsearch,
                lazo_client=self.application.lazo_client,
                data_profile=data_profile,
                query_args_main=None,
                query_args_sup=None,
                tabular_variables=None,
                dataset_id=task['id'],
                union=False
            )

            if not search_results:
                return self.send_error_json(
                    400,
                    "The Datamart dataset referenced by 'task' cannot augment "
                    "'data'",
                )

            task = search_results[0]

            with get_dataset(metadata, task['id'], format='csv') as newdata:
                # perform augmentation
                logger.info("Performing half-augmentation with supplied data")
                new_path = augment(
                    data,
                    newdata,
                    data_profile,
                    task,
                    return_only_datamart_data=True
                )
                # FIXME: This always sends in D3M format

            # send a zip file
            self.set_header('Content-Type', 'application/zip')
            self.set_header(
                'Content-Disposition',
                'attachment; filename="augmentation.zip"')
            logger.info("Sending ZIP...")
            writer = RecursiveZipWriter(self.write)
            writer.write_recursive(new_path)
            writer.close()
            shutil.rmtree(os.path.abspath(os.path.join(new_path, '..')))


class Metadata(BaseHandler, GracefulHandler):
    @PROM_METADATA_TIME.time()
    def get(self, dataset_id):
        PROM_METADATA.inc()

        es = self.application.elasticsearch
        try:
            metadata = es.get('datamart', dataset_id)['_source']
        except elasticsearch.NotFoundError:
            raise HTTPError(404)

        result = {'id': dataset_id, 'metadata': metadata}
        result = enhance_metadata(result)
        return self.send_json(result)


class Augment(BaseHandler, GracefulHandler, ProfilePostedData):
    @PROM_AUGMENT_TIME.time()
    def post(self):
        PROM_AUGMENT.inc()

        type_ = self.request.headers.get('Content-type', '')
        if not type_.startswith('multipart/form-data'):
            return self.send_error_json(400, "Use multipart/form-data to send "
                                             "the 'data' file and 'task' JSON")

        task = self.get_body_argument('task', None)
        if task is None and 'task' in self.request.files:
            task = self.request.files['task'][0].body.decode('utf-8')
        if task is None:
            return self.send_error_json(400, "Missing 'task' JSON")
        task = json.loads(task)

        data = self.get_body_argument('data', None)
        if data is not None:
            data = data.encode('utf-8')
        elif 'data' in self.request.files:
            data = self.request.files['data'][0].body
        else:
            return self.send_error_json(400, "Missing 'data'")

        columns = self.get_body_argument('columns', None)
        if 'columns' in self.request.files:
            columns = self.request.files['columns'][0].body.decode('utf-8')
        if columns is not None:
            columns = json.loads(columns)

        logger.info("Got augmentation, content-type=%r", type_.split(';')[0])

        # data
        try:
            data_profile, data_hash = self.handle_data_parameter(data)
        except ClientError as e:
            return self.send_error_json(400, str(e))

        # materialize augmentation data
        metadata = task['metadata']

        # no augmentation task provided -- will first look for possible augmentation
        if task['augmentation']['type'] == 'none':
            logger.info("No task, searching for augmentations")
            search_results = get_augmentation_search_results(
                es=self.application.elasticsearch,
                lazo_client=self.application.lazo_client,
                data_profile=data_profile,
                query_args_main=None,
                query_args_sup=None,
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
                return self.send_error_json(400,
                                            "The Datamart dataset referenced "
                                            "by 'task' cannot augment 'data'")

        key = hash_json(
            task=task,
            supplied_data=data_hash,
            version=os.environ['DATAMART_VERSION'],
            columns=columns,
        )

        def create_aug(cache_temp):
            try:
                with get_dataset(metadata, task['id'], format='csv') as newdata:
                    # perform augmentation
                    logger.info("Performing augmentation with supplied data")
                    augment(
                        data,
                        newdata,
                        data_profile,
                        task,
                        columns=columns,
                        destination=cache_temp,
                    )
            except AugmentationError as e:
                return self.send_error_json(400, str(e))

        with cache_get_or_set('/cache/aug', key, create_aug) as path:
            # send a zip file
            self.set_header('Content-Type', 'application/zip')
            self.set_header(
                'Content-Disposition',
                'attachment; filename="augmentation.zip"')
            logger.info("Sending ZIP...")
            writer = RecursiveZipWriter(self.write)
            # FIXME: This will write the whole thing to Tornado's buffer
            # Maybe compressing to disk and streaming that file is better?
            writer.write_recursive(path)
            writer.close()

        return self.finish()


class Version(BaseHandler):
    def get(self):
        return self.send_json({
            'version': os.environ['DATAMART_VERSION'].lstrip('v'),
            'min_profiler_version': datamart_profiler.__version__,
        })


class Health(BaseHandler):
    def get(self):
        if self.application.is_closing:
            self.set_status(503, reason="Shutting down")
            return self.finish('shutting down')
        else:
            return self.finish('ok')


class Application(GracefulApplication):
    def __init__(self, *args, es, redis_client, lazo, **kwargs):
        super(Application, self).__init__(*args, **kwargs)

        self.is_closing = False

        self.elasticsearch = es
        self.redis = redis_client
        self.lazo_client = lazo
        self.nominatim = os.environ['NOMINATIM_URL']
        self.channel = None

        log_future(asyncio.get_event_loop().create_task(self._amqp()), logger)

    async def _amqp(self):
        connection = await aio_pika.connect_robust(
            host=os.environ['AMQP_HOST'],
            login=os.environ['AMQP_USER'],
            password=os.environ['AMQP_PASSWORD'],
        )
        self.channel = await connection.channel()
        await self.channel.set_qos(prefetch_count=1)

    def log_request(self, handler):
        if handler.request.path == '/health':
            return
        super(Application, self).log_request(handler)


def make_app(debug=False):
    es = elasticsearch.Elasticsearch(
        os.environ['ELASTICSEARCH_HOSTS'].split(',')
    )
    redis_client = redis.Redis(host=os.environ['REDIS_HOST'])
    lazo_client = lazo_index_service.LazoIndexClient(
        host=os.environ['LAZO_SERVER_HOST'],
        port=int(os.environ['LAZO_SERVER_PORT'])
    )

    return Application(
        [
            URLSpec('/profile', Profile, name='profile'),
            URLSpec('/search', Search, name='search'),
            URLSpec('/download/([^/]+)', DownloadId, name='download_id'),
            URLSpec('/download', Download, name='download'),
            URLSpec('/metadata/([^/]+)', Metadata, name='metadata'),
            URLSpec('/augment', Augment, name='augment'),
            URLSpec('/version', Version, name='version'),
            URLSpec('/health', Health, name='health'),
        ],
        debug=debug,
        serve_traceback=True,
        es=es,
        redis_client=redis_client,
        lazo=lazo_client
    )


def main():
    setup_logging()
    prometheus_client.start_http_server(8000)
    logger.info("Startup: query %s", os.environ['DATAMART_VERSION'])

    app = make_app()
    app.listen(8002, xheaders=True, max_buffer_size=2147483648)
    loop = tornado.ioloop.IOLoop.current()
    loop.start()
