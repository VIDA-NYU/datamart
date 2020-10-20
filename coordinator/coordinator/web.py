import asyncio
import elasticsearch
import logging
import jinja2
import json
import os
import pkg_resources
import prometheus_client
from tornado.httpclient import AsyncHTTPClient
import tornado.ioloop
from tornado.routing import URLSpec
import tornado.web

from datamart_core.common import setup_logging

from .cache import check_cache
from .coordinator import Coordinator


logger = logging.getLogger(__name__)


class BaseHandler(tornado.web.RequestHandler):
    """Base class for all request handlers.
    """
    application: 'Application'

    template_env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(
            [pkg_resources.resource_filename('coordinator',
                                             'templates')]
        ),
        autoescape=jinja2.select_autoescape(('html', 'htm')),
    )

    @jinja2.contextfunction
    def _tpl_static_url(context, path):
        v = not context['handler'].application.settings.get('debug', False)
        return context['handler'].static_url(path, include_version=v)
    template_env.globals['static_url'] = _tpl_static_url

    @jinja2.contextfunction
    def _tpl_reverse_url(context, path, *args):
        return context['handler'].reverse_url(path, *args)
    template_env.globals['reverse_url'] = _tpl_reverse_url

    @jinja2.contextfunction
    def _tpl_xsrf_form_html(context):
        return jinja2.Markup(context['handler'].xsrf_form_html())
    template_env.globals['xsrf_form_html'] = _tpl_xsrf_form_html

    def set_default_headers(self):
        self.set_header('Server', 'Auctus/%s' % os.environ['DATAMART_VERSION'])

    def render_string(self, template_name, **kwargs):
        template = self.template_env.get_template(template_name)
        return template.render(
            handler=self,
            current_user=self.current_user,
            api_url=os.environ.get('API_URL', ''),
            **kwargs)

    def send_json(self, obj):
        if isinstance(obj, list):
            obj = {'results': obj}
        elif not isinstance(obj, dict):
            raise ValueError("Can't encode %r to JSON" % type(obj))
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        return self.finish(json.dumps(obj))

    http_client = AsyncHTTPClient(defaults=dict(user_agent="Datamart"))

    def get_current_user(self):
        return self.get_secure_cookie('user')

    @property
    def coordinator(self):
        return self.application.coordinator


class Index(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        self.render('index.html')


class Login(BaseHandler):
    def get(self):
        if self.current_user:
            return self._go_to_next()
        else:
            return self.render(
                'login.html',
                next=self.get_argument('next', ''),
            )

    def post(self):
        password = self.get_body_argument('password')
        if password == self.application.admin_password:
            logger.info("Admin logged in")
            self.set_secure_cookie('user', 'admin')
            return self._go_to_next()
        else:
            self.render(
                'login.html',
                next=self.get_argument('next', ''),
                error="Invalid password",
            )

    def _go_to_next(self):
        next_ = self.get_argument('next', '')
        if not next_:
            next_ = self.reverse_url('index')
        return self.redirect(next_)


class Statistics(BaseHandler):
    def prepare(self):
        super(BaseHandler, self).prepare()
        self.set_header('Access-Control-Allow-Origin', '*')
        self.set_header('Access-Control-Allow-Methods', 'GET')

    def get(self):
        return self.send_json({
            'recent_discoveries': self.coordinator.recent_discoveries,
            'sources_counts': self.coordinator.sources_counts,
        })


class CustomErrorHandler(tornado.web.ErrorHandler, BaseHandler):
    pass


class Application(tornado.web.Application):
    def __init__(self, *args, es, **kwargs):
        super(Application, self).__init__(*args, **kwargs)

        self.elasticsearch = es
        self.coordinator = Coordinator(self.elasticsearch)
        self.admin_password = os.environ['ADMIN_PASSWORD']


def make_app(debug=False):
    if 'XDG_CACHE_HOME' in os.environ:
        cache = os.environ['XDG_CACHE_HOME']
    else:
        cache = os.path.expanduser('~/.cache')
    os.makedirs(cache, 0o700, exist_ok=True)
    cache = os.path.join(cache, 'datamart.json')
    secret = None
    try:
        fp = open(cache)
    except IOError:
        pass
    else:
        try:
            secret = json.load(fp)['cookie_secret']
            fp.close()
        except Exception:
            logger.exception("Couldn't load cookie secret from cache file")
        if not isinstance(secret, str) or not 10 <= len(secret) < 2048:
            logger.error("Invalid cookie secret in cache file")
            secret = None
    if secret is None:
        secret = os.urandom(30).decode('iso-8859-15')
        try:
            with open(cache, 'w') as fp:
                json.dump({'cookie_secret': secret}, fp)
        except IOError:
            logger.error("Couldn't open cache file, cookie secret won't be "
                         "persisted! Users will be logged out if you restart "
                         "the program.")

    es = elasticsearch.Elasticsearch(
        os.environ['ELASTICSEARCH_HOSTS'].split(',')
    )

    return Application(
        [
            URLSpec('/api/statistics', Statistics),
            URLSpec('/', Index, name='index'),
            URLSpec('/login', Login, name='login'),
        ],
        static_path=pkg_resources.resource_filename('coordinator',
                                                    'static'),
        login_url='/login',
        xsrf_cookies=True,
        debug=debug,
        cookie_secret=secret,
        es=es,
        default_handler_class=CustomErrorHandler,
        default_handler_args={"status_code": 404},
    )


def main():
    setup_logging()
    debug = os.environ.get('DEBUG') not in (None, '', 'no', 'off', 'false')
    prometheus_client.start_http_server(8000)
    logger.info("Startup: coordinator %s", os.environ['DATAMART_VERSION'])
    if debug:
        logger.error("Debug mode is ON")

    app = make_app(debug)
    app.listen(8003, xheaders=True, max_buffer_size=2147483648)
    loop = tornado.ioloop.IOLoop.current()
    if debug:
        asyncio.get_event_loop().set_debug(True)
    check_cache()  # Schedules itself to run periodically
    loop.start()
