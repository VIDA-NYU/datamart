import aio_pika
import asyncio
import logging
import json
import os
from tornado.httpclient import AsyncHTTPClient
from tornado.web import HTTPError, RequestHandler
from urllib.parse import urlencode
import zipfile

from datamart_core.common import log_future
from datamart_geo import GeoData
from datamart_materialize import get_writer

from .graceful_shutdown import GracefulApplication


logger = logging.getLogger(__name__)


BUCKETS = [0.5, 1.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0, 300.0, 600.0]


class BaseHandler(RequestHandler):
    """Base class for all request handlers.
    """
    application: 'Application'

    def set_default_headers(self):
        self.set_header('Server', 'Auctus/%s' % os.environ['DATAMART_VERSION'])

    def get_json(self):
        type_ = self.request.headers.get('Content-Type', '')
        if not type_.startswith('application/json'):
            self.send_error_json(400, "Expected JSON")
            raise HTTPError(400)
        try:
            return json.loads(self.request.body.decode('utf-8'))
        except UnicodeDecodeError:
            self.send_error_json(400, "Invalid character encoding")
            raise HTTPError(400)
        except json.JSONDecodeError:
            self.send_error_json(400, "Invalid JSON")
            raise HTTPError(400)

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

    async def send_file(self, path, name):
        if zipfile.is_zipfile(path):
            type_ = 'application/zip'
            name += '.zip'
        else:
            type_ = 'application/octet-stream'
        self.set_header('Content-Type', type_)
        self.set_header('X-Content-Type-Options', 'nosniff')
        self.set_header('Content-Disposition',
                        'attachment; filename="%s"' % name)
        logger.info("Sending file...")
        with open(path, 'rb') as fp:
            self.set_header('Content-Length', fp.seek(0, 2))
            fp.seek(0, 0)

            BUFSIZE = 40960
            buf = fp.read(BUFSIZE)
            while buf:
                self.write(buf)
                if len(buf) != BUFSIZE:
                    break
                buf = fp.read(BUFSIZE)
                await self.flush()
            return await self.finish()

    def prepare(self):
        super(BaseHandler, self).prepare()
        self.set_header('Access-Control-Allow-Origin', '*')
        self.set_header('Access-Control-Allow-Methods', 'POST')
        self.set_header('Access-Control-Allow-Headers', 'Content-Type')

    def options(self):
        # CORS pre-flight
        self.set_status(204)
        return self.finish()

    def validate_format(self, format, format_options):
        writer_cls = get_writer(format)
        format_ext = None
        if hasattr(writer_cls, 'parse_options'):
            format_options = writer_cls.parse_options(format_options)
        elif format_options:
            self.send_error_json(400, "Invalid output options")
            raise HTTPError(400)
        if hasattr(writer_cls, 'extension'):
            format_ext = writer_cls.extension
        return format, format_options, format_ext

    def read_format(self, default_format='csv'):
        format = self.get_query_argument('format', default_format)
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

        return self.validate_format(format, format_options)

    @staticmethod
    def serialize_format(format, format_options):
        dct = {'format': format}
        for k, v in format_options.items():
            dct['format_' + k] = v
        return urlencode(dct)

    http_client = AsyncHTTPClient(defaults=dict(user_agent="Datamart"))


class Application(GracefulApplication):
    def __init__(self, *args, es, redis_client, lazo, **kwargs):
        super(Application, self).__init__(*args, **kwargs)

        self.is_closing = False

        self.frontend_url = os.environ['FRONTEND_URL']
        self.api_url = os.environ['API_URL']
        self.elasticsearch = es
        self.redis = redis_client
        self.lazo_client = lazo
        if os.environ.get('NOMINATIM_URL'):
            self.nominatim = os.environ['NOMINATIM_URL']
        else:
            self.nominatim = None
            logger.warning(
                "$NOMINATIM_URL is not set, not resolving URLs"
            )
        self.geo_data = GeoData.from_local_cache()
        self.channel = None

        self.custom_fields = {}
        custom_fields = os.environ.get('CUSTOM_FIELDS', None)
        if custom_fields:
            custom_fields = json.loads(custom_fields)
            if custom_fields:
                for field, opts in custom_fields.items():
                    opts.setdefault('label', field)
                    opts.setdefault('required', False)
                    opts.setdefault('type', 'text')
                    if (
                        not opts.keys() <= {'label', 'type', 'required'}
                        or not isinstance(opts['label'], str)
                        or not isinstance(opts['required'], bool)
                        or not isinstance(opts['type'], str)
                        or opts['type'] not in ('integer', 'text', 'keyword')
                    ):
                        raise ValueError("Invalid custom field %s" % field)

                self.custom_fields = custom_fields
                logger.info(
                    "Custom fields: %s",
                    ", ".join(self.custom_fields.keys()),
                )

        self.geo_data.load_areas([0, 1, 2], bounds=True)

        self.sources_counts = {}
        self.recent_discoveries = []

        asyncio.get_event_loop().run_until_complete(
            asyncio.get_event_loop().create_task(self._amqp())
        )

    async def _amqp(self):
        connection = await aio_pika.connect_robust(
            host=os.environ['AMQP_HOST'],
            port=int(os.environ['AMQP_PORT']),
            login=os.environ['AMQP_USER'],
            password=os.environ['AMQP_PASSWORD'],
        )
        self.channel = await connection.channel()
        await self.channel.set_qos(prefetch_count=1)

        # Declare profiling exchange (to publish datasets via upload)
        self.profile_exchange = await self.channel.declare_exchange(
            'profile',
            aio_pika.ExchangeType.FANOUT,
        )

        # Start statistics-fetching coroutine
        log_future(
            asyncio.get_event_loop().create_task(self.update_statistics()),
            logger,
            should_never_exit=True,
        )

    async def update_statistics(self):
        http_client = AsyncHTTPClient()
        while True:
            try:
                # Get counts from coordinator
                response = await http_client.fetch(
                    'http://coordinator:8003/api/statistics',
                )
                statistics = json.loads(response.body.decode('utf-8'))
            except Exception:
                logger.exception("Can't get statistics from coordinator")
            else:
                self.sources_counts = statistics['sources_counts']
                self.recent_discoveries = statistics['recent_discoveries']

            await asyncio.sleep(60)

    def log_request(self, handler):
        if handler.request.path == '/health':
            return
        super(Application, self).log_request(handler)
