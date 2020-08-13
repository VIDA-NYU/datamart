import elasticsearch
import lazo_index_service
import logging
import os
import prometheus_client
import redis
import tornado.ioloop
from tornado.routing import URLSpec
import tornado.httputil
import tornado.web

from datamart_core.common import setup_logging
from datamart_core.prom import PromMeasureRequest
import datamart_profiler

from .augment import Augment, AugmentResult
from .base import BUCKETS, BaseHandler, Application
from .download import DownloadId, Download, Metadata
from .profile import Profile
from .search import Search
from .sessions import SessionNew, SessionGet
from .upload import Upload


logger = logging.getLogger(__name__)


PROM_LOCATION = PromMeasureRequest(
    count=prometheus_client.Counter(
        'req_location_count',
        "Location search requests",
    ),
    time=prometheus_client.Histogram(
        'req_location_seconds',
        "Location search request time",
        buckets=BUCKETS,
    ),
)
PROM_STATISTICS = PromMeasureRequest(
    count=prometheus_client.Counter(
        'req_statistics_count',
        "Statistics requests",
    ),
    time=prometheus_client.Histogram(
        'req_statistics_seconds',
        "Statistics request time",
        buckets=BUCKETS,
    ),
)
PROM_VERSION = PromMeasureRequest(
    count=prometheus_client.Counter(
        'req_version_count',
        "Version requests",
    ),
    time=prometheus_client.Histogram(
        'req_version_seconds',
        "Version request time",
        buckets=BUCKETS,
    ),
)


class LocationSearch(BaseHandler):
    @PROM_LOCATION.sync()
    def post(self):
        query = self.get_body_argument('q').strip()
        geo_data = self.application.geo_data
        areas = geo_data.resolve_names([query.lower()])
        areas = [area for area in areas if area is not None]
        if areas and areas[0]:
            bounds = geo_data.get_bounds(areas[0].area)
            logger.info("Resolved area %r to %r", query, areas[0].area)
            return self.send_json({'results': [
                {
                    'area': areas[0].area,
                    'boundingbox': bounds,
                }
            ]})
        else:
            return self.send_json({'results': []})


class Statistics(BaseHandler):
    @PROM_STATISTICS.sync()
    def get(self):
        return self.send_json({
            'recent_discoveries': self.application.recent_discoveries,
            'sources_counts': self.application.sources_counts,
            'custom_fields': self.application.custom_fields,
        })


class Version(BaseHandler):
    @PROM_VERSION.sync()
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
            URLSpec('/augment/([^/]+)', AugmentResult, name='augment_result'),
            URLSpec('/upload', Upload, name='upload'),
            URLSpec('/session/new', SessionNew, name='session_new'),
            URLSpec('/session/([^/]+)', SessionGet, name='session_get'),
            URLSpec('/location', LocationSearch, name='location_search'),
            URLSpec('/statistics', Statistics, name='statistics'),
            URLSpec('/version', Version, name='version'),
            URLSpec('/health', Health, name='health'),
        ],
        debug=debug,
        es=es,
        redis_client=redis_client,
        lazo=lazo_client,
    )


def main():
    setup_logging()
    debug = os.environ.get('DEBUG') not in (None, '', 'no', 'off', 'false')
    prometheus_client.start_http_server(8000)
    logger.info("Startup: apiserver %s", os.environ['DATAMART_VERSION'])
    if debug:
        logger.error("Debug mode is ON")

    app = make_app(debug)
    app.listen(8002, xheaders=True, max_buffer_size=2147483648)
    loop = tornado.ioloop.IOLoop.current()
    loop.start()
