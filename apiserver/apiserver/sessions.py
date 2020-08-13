import logging
import json
from urllib.parse import urlencode
import uuid

import prometheus_client

from datamart_core.prom import PromMeasureRequest
from .base import BUCKETS, BaseHandler
from .profile import profile_token_re


logger = logging.getLogger(__name__)


PROM_SESSION_NEW = PromMeasureRequest(
    count=prometheus_client.Counter(
        'req_session_new_count',
        "New session requests",
    ),
    time=prometheus_client.Histogram(
        'req_session_new_seconds',
        "New session request time",
        buckets=BUCKETS,
    ),
)
PROM_SESSION_GET = PromMeasureRequest(
    count=prometheus_client.Counter(
        'req_session_get_count',
        "Get session requests",
    ),
    time=prometheus_client.Histogram(
        'req_session_get_seconds',
        "Get session request time",
        buckets=BUCKETS,
    ),
)


class SessionNew(BaseHandler):
    @PROM_SESSION_NEW.sync()
    def post(self):
        # Read input
        session = self.get_json()
        data_token = session.pop('data_token', None)
        if data_token is not None and (
            not isinstance(data_token, str)
            or not profile_token_re.match(data_token)
        ):
            return self.send_error_json(
                400,
                "Invalid data_token",
            )
        format, format_options, _ = self.validate_format(
            session.pop('format', 'csv'),
            session.pop('format_options', {}),
        )
        system_name = session.pop('system_name', 'TA3')
        if session:
            return self.send_error_json(
                400,
                "Unrecognized key %r" % next(iter(session)),
            )

        # Build an ID for the session
        session_id = str(uuid.uuid4())

        # Build our session object
        session = {
            'session_id': session_id,
            'format': format,
            'format_options': format_options,
            'system_name': system_name,
        }
        if data_token:
            session['data_token'] = data_token

        # Build a link for the user's browser
        session_json = json.dumps(
            session,
            # Compact
            sort_keys=True, indent=None, separators=(',', ':'),
        )
        link_url = (
            self.application.frontend_url
            + '/?'
            + urlencode({'session': session_json})
        )

        return self.send_json({
            # Send the session ID to TA3, used to retrieve results
            'session_id': session_id,
            # Send the JSON info to the frontend
            'link_url': link_url,
        })


class SessionGet(BaseHandler):
    @PROM_SESSION_GET.sync()
    def get(self, session_id):
        # Get session from Redis
        datasets = self.application.redis.lrange(
            'session:' + session_id,
            0, -1,
        )

        api_url = self.application.api_url
        results = []
        for record in datasets:
            record = json.loads(record.decode('utf-8'))
            results.append({
                'url': api_url + record['url'],
                'type': record['type'],
            })

        return self.send_json({'results': results})
