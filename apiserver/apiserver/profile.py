import elasticsearch
import logging
import hashlib
import json
import os
import prometheus_client
import re
import tempfile
import time
import tornado.web

from datamart_core.fscache import cache_get_or_set
from datamart_core.materialize import detect_format_convert_to_csv
from datamart_core.prom import PromMeasureRequest
from datamart_profiler import process_dataset

from .base import BUCKETS, BaseHandler
from .graceful_shutdown import GracefulHandler


logger = logging.getLogger(__name__)


PROM_PROFILE = PromMeasureRequest(
    count=prometheus_client.Counter(
        'req_profile_count',
        "Profile requests",
    ),
    time=prometheus_client.Histogram(
        'req_profile_seconds',
        "Profile request time",
        buckets=BUCKETS,
    ),
)


class ProfilePostedData(tornado.web.RequestHandler):
    def handle_data_parameter(self, data):
        """
        Handles the 'data' parameter.

        :param data: the input parameter
        :return: (data, data_profile)
          data: data as bytes (either the input or loaded from the input)
          data_profile: the profiling (metadata) of the data
        """

        if not isinstance(data, bytes):
            raise ValueError

        # Use SHA1 of file as cache key
        sha1 = hashlib.sha1(data)
        data_hash = sha1.hexdigest()

        data_profile = self.application.redis.get('profile:' + data_hash)

        # Do format conversion
        materialize = {}

        def create_csv(cache_temp):
            with open(cache_temp, 'wb') as fp:
                fp.write(data)

            def convert_dataset(func, path):
                with tempfile.NamedTemporaryFile(
                    prefix='.convert',
                    dir='/cache/user_data',
                ) as tmpfile:
                    os.rename(path, tmpfile.name)
                    with open(path, 'w', newline='') as dst:
                        func(tmpfile.name, dst)
                    return path

            ret = detect_format_convert_to_csv(
                cache_temp,
                convert_dataset,
                materialize,
            )
            assert ret == cache_temp

        with cache_get_or_set(
            '/cache/user_data',
                data_hash,
                create_csv,
        ) as csv_path:
            if data_profile is not None:
                # This is here because we want to put the data in the cache
                # even if the profile is already in Redis
                logger.info("Found cached profile_data")
                data_profile = json.loads(data_profile)
            else:
                logger.info("Profiling...")
                start = time.perf_counter()
                with open(csv_path, 'rb') as data:
                    data_profile = process_dataset(
                        data=data,
                        lazo_client=self.application.lazo_client,
                        nominatim=self.application.nominatim,
                        geo_data=self.application.geo_data,
                        search=True,
                        include_sample=True,
                        coverage=True,
                    )
                logger.info("Profiled in %.2fs", time.perf_counter() - start)

                data_profile['materialize'] = materialize
                data_profile['version'] = os.environ['DATAMART_VERSION']

                self.application.redis.set(
                    'profile:' + data_hash,
                    json.dumps(
                        data_profile,
                        # Compact
                        sort_keys=True, indent=None, separators=(',', ':'),
                    ),
                )

        return data_profile, data_hash


def get_data_profile_from_es(es, dataset_id):
    try:
        data_profile = es.get('datamart', dataset_id)['_source']
    except elasticsearch.NotFoundError:
        return None

    # Get Lazo sketches from Elasticsearch
    # FIXME: Add support for this in Lazo instead
    for col in data_profile['columns']:
        try:
            sketch = es.get(
                'lazo',
                '%s__.__%s' % (dataset_id, col['name']),
            )['_source']
        except elasticsearch.NotFoundError:
            pass
        else:
            col['lazo'] = dict(
                n_permutations=int(sketch['n_permutations']),
                hash_values=[int(e) for e in sketch['hash']],
                cardinality=int(sketch['cardinality']),
            )

    return data_profile


class Profile(BaseHandler, GracefulHandler, ProfilePostedData):
    @PROM_PROFILE.sync()
    def post(self):
        data = self.get_body_argument('data', None)
        if 'data' in self.request.files:
            data = self.request.files['data'][0].body
        elif data is not None:
            data = data.encode('utf-8')

        if len(data) == 40:
            try:
                data_hash = data.decode('ascii')
            except UnicodeDecodeError:
                pass
            else:
                if profile_token_re.match(data_hash):
                    data_profile = self.application.redis.get(
                        'profile:' + data_hash
                    )
                    if data_profile:
                        return self.send_json(dict(
                            json.loads(data_profile),
                            token=data_hash,
                        ))
                    else:
                        return self.send_error_json(
                            404,
                            "Data profile token expired",
                        )

        if data is None:
            return self.send_error_json(
                400,
                "Please send 'data' as a file, using multipart/form-data",
            )

        logger.info("Got profile")

        data_profile, data_hash = self.handle_data_parameter(data)

        return self.send_json(dict(
            data_profile,
            version=os.environ['DATAMART_VERSION'],
            token=data_hash,
        ))


profile_token_re = re.compile(r'^[0-9a-f]{40}$')
