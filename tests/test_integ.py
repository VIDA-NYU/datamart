import elasticsearch
import io
import json
import jsonschema
import os
import pkg_resources
import re
import requests
import tempfile
import time
import yaml
import zipfile

import datamart_materialize

from .test_profile import check_ranges, check_geo_ranges, check_plot
from .utils import DataTestCase, data


schemas = os.path.join(os.path.dirname(__file__), '..', 'docs', 'schemas')
schemas = os.path.abspath(schemas)


# https://github.com/Julian/jsonschema/issues/343
def _fix_refs(obj, name):
    if isinstance(obj, dict):
        return {
            k: _fix_refs(v, name) if k != '$ref' else 'file://%s/%s%s' % (schemas, name, v)
            for k, v in obj.items()
        }
    elif isinstance(obj, list):
        return [_fix_refs(v, name) for v in obj]
    else:
        return obj


with open(os.path.join(schemas, 'query_result_schema.json')) as fp:
    result_schema = json.load(fp)
result_schema = _fix_refs(result_schema, 'query_result_schema.json')
result_list_schema = {
    'type': 'object',
    'properties': {
        'results': {'type': 'array', 'items': result_schema}
    },
    'definitions': result_schema.pop('definitions'),
}


class DatamartTest(DataTestCase):
    def datamart_get(self, url, **kwargs):
        return self._request('get', url, **kwargs)

    def datamart_post(self, url, **kwargs):
        return self._request('post', url, **kwargs)

    def _request(self, method, url, schema=None, check_status=True, **kwargs):
        response = requests.request(
            method,
            os.environ['QUERY_HOST'] + url,
            **kwargs
        )
        for _ in range(5):
            if response.status_code != 503:
                break
            time.sleep(0.5)
            response = requests.request(
                method,
                os.environ['QUERY_HOST'] + url,
                **kwargs
            )
        else:
            response.raise_for_status()
        if check_status:
            self.assert_response(response)
        if schema is not None:
            jsonschema.validate(response.json(), schema)
        return response

    def assert_response(self, response):
        if response.status_code == 400:  # pragma: no cover
            try:
                error = response.json()['error']
            except (KeyError, ValueError):
                error = "(not JSON)"
            self.fail("Error 400 from server: %s" % error)
        response.raise_for_status()


class TestProfiler(DataTestCase):
    def test_basic(self):
        es = elasticsearch.Elasticsearch(
            os.environ['ELASTICSEARCH_HOSTS'].split(',')
        )
        hits = es.search(
            index='datamart',
            body={
                'query': {
                    'match_all': {},
                },
            },
        )['hits']['hits']
        hits = {h['_id']: h['_source'] for h in hits}

        self.assertJson(
            hits,
            {
                'datamart.test.basic': basic_metadata,
                'datamart.test.geo': geo_metadata,
                'datamart.test.agg': agg_metadata,
                'datamart.test.lazo': lazo_metadata,
                'datamart.test.daily': daily_metadata,
                'datamart.test.hourly': hourly_metadata,
            },
        )

    def test_indexes(self):
        response = requests.get(
            'http://' + os.environ['ELASTICSEARCH_HOSTS'].split(',')[0] +
            '/_all'
        )
        response.raise_for_status()
        actual = response.json()
        with pkg_resources.resource_stream(
                'coordinator', 'elasticsearch.yml') as stream:
            expected = yaml.safe_load(stream)
        actual.pop('lazo', None)
        for index in expected.values():
            index.setdefault('aliases', {})
        for index in actual.values():
            index.pop('settings', None)
        self.assertEqual(actual, expected)


class TestProfileQuery(DatamartTest):
    def test_basic(self):
        with data('basic.csv') as basic_fp:
            response = self.datamart_post(
                '/profile',
                files={'data': basic_fp}
            )
        # Some fields like 'name', 'description' won't be there
        metadata = {k: v for k, v in basic_metadata.items()
                    if k not in {'id', 'name', 'description', 'source',
                                 'date', 'materialize', 'sample'}}
        # Plots are not computed, remove them too
        metadata['columns'] = [
            {k: v for k, v in col.items() if k != 'plot'}
            for col in metadata['columns']
        ]
        # Handle lazo data
        check_lazo = lambda dct: (
            dct.keys() == {'cardinality', 'hash_values', 'n_permutations'}
        )
        metadata['columns'][0]['lazo'] = check_lazo
        metadata['columns'][1]['lazo'] = check_lazo
        metadata['columns'][2]['lazo'] = check_lazo
        # Expect token
        metadata['token'] = 'cac18c69aff995773bed73273421365006e5e0b6'

        self.assertJson(response.json(), metadata)


class TestSearch(DatamartTest):
    def test_basic_search_json(self):
        """Basic search, posting the query as JSON."""
        @self.do_test_basic_search
        def query():
            response = self.datamart_post(
                '/search',
                json={'keywords': ['people']},
                schema=result_list_schema,
            )
            self.assertEqual(response.request.headers['Content-Type'],
                             'application/json')
            return response

    def test_basic_search_formdata(self):
        """Basic search, posting the query as formdata-urlencoded."""
        @self.do_test_basic_search
        def query():
            response = self.datamart_post(
                '/search',
                data={'query': json.dumps({'keywords': ['people']})},
                schema=result_list_schema,
            )
            self.assertEqual(response.request.headers['Content-Type'],
                             'application/x-www-form-urlencoded')
            return response

    def test_basic_search_file(self):
        """Basic search, posting the query as a file in multipart/form-data."""
        @self.do_test_basic_search
        def query():
            response = self.datamart_post(
                '/search',
                files={'query': json.dumps({'keywords': ['people']})
                       .encode('utf-8')},
                schema=result_list_schema,
            )
            self.assertEqual(
                response.request.headers['Content-Type'].split(';', 1)[0],
                'multipart/form-data',
            )
            return response

    def do_test_basic_search(self, query_func):
        response = query_func()
        results = response.json()['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], 'datamart.test.basic')
        self.assertJson(
            results[0],
            {
                'id': 'datamart.test.basic',
                'augmentation': {
                    'type': 'none',
                    'left_columns': [],
                    'right_columns': [],
                    'left_columns_names': [],
                    'right_columns_names': []
                },
                'score': lambda n: isinstance(n, float),
                'metadata': basic_metadata,
                'd3m_dataset_description': basic_metadata_d3m('4.0.0'),
                'supplied_id': None,
                'supplied_resource_id': None
            },
        )

    def test_search_with_source(self):
        """Search restricted by source."""
        response = self.datamart_post(
            '/search',
            json={'keywords': ['people'], 'source': ['remi']},
            schema=result_list_schema,
        )
        results = response.json()['results']
        self.assertEqual(
            {r['id'] for r in results},
            {'datamart.test.basic'},
        )

        # Wrong source
        response = self.datamart_post(
            '/search',
            json={'keywords': ['people'], 'source': ['fernando']},
            schema=result_list_schema,
        )
        results = response.json()['results']
        self.assertEqual(
            {r['id'] for r in results},
            set(),
        )

        # All datasets from given source
        response = self.datamart_post(
            '/search',
            json={'source': ['fernando']},
            schema=result_list_schema,
        )
        results = response.json()['results']
        self.assertEqual(
            {r['id'] for r in results},
            {'datamart.test.agg', 'datamart.test.lazo'},
        )


class TestDataSearch(DatamartTest):
    def test_basic_join(self):
        query = {'keywords': ['people']}

        with data('basic_aug.csv') as basic_aug:
            response = self.datamart_post(
                '/search',
                files={
                    'query': json.dumps(query).encode('utf-8'),
                    'data': basic_aug,
                },
                schema=result_list_schema,
            )
        results = response.json()['results']
        self.assertJson(
            results,
            [
                {
                    'id': 'datamart.test.basic',
                    'metadata': basic_metadata,
                    'd3m_dataset_description': basic_metadata_d3m('4.0.0'),
                    'score': lambda n: isinstance(n, float) and n > 0.0,
                    'augmentation': {
                        'left_columns': [[0]],
                        'left_columns_names': [['number']],
                        'right_columns': [[2]],
                        'right_columns_names': [['number']],
                        'type': 'join'
                    },
                    'supplied_id': None,
                    'supplied_resource_id': None
                }
            ]
        )

    def test_basic_join_only_data(self):
        with data('basic_aug.csv') as basic_aug:
            response = self.datamart_post(
                '/search',
                files={
                    'data': basic_aug,
                },
                schema=result_list_schema,
            )
        results = response.json()['results']
        self.assertJson(
            results,
            [
                {
                    'id': 'datamart.test.basic',
                    'metadata': basic_metadata,
                    'd3m_dataset_description': basic_metadata_d3m('4.0.0'),
                    'score': lambda n: isinstance(n, float) and n > 0.0,
                    'augmentation': {
                        'left_columns': [[0]],
                        'left_columns_names': [['number']],
                        'right_columns': [[2]],
                        'right_columns_names': [['number']],
                        'type': 'join'
                    },
                    'supplied_id': None,
                    'supplied_resource_id': None
                }
            ]
        )

    def test_basic_join_only_data_csv(self):
        with data('basic_aug.csv') as basic_aug:
            response = self.datamart_post(
                '/search',
                data=basic_aug,
                headers={'Content-type': 'text/csv'},
                schema=result_list_schema,
            )
        results = response.json()['results']
        self.assertJson(
            results,
            [
                {
                    'id': 'datamart.test.basic',
                    'metadata': basic_metadata,
                    'd3m_dataset_description': basic_metadata_d3m('4.0.0'),
                    'score': lambda n: isinstance(n, float) and n > 0.0,
                    'augmentation': {
                        'left_columns': [[0]],
                        'left_columns_names': [['number']],
                        'right_columns': [[2]],
                        'right_columns_names': [['number']],
                        'type': 'join'
                    },
                    'supplied_id': None,
                    'supplied_resource_id': None
                }
            ]
        )

    def test_basic_join_only_profile(self):
        with data('basic_aug.csv') as basic_aug:
            response = self.datamart_post(
                '/profile',
                files={'data': basic_aug},
            )
        profile = response.json()

        response = self.datamart_post(
            '/search',
            files={
                'data_profile': json.dumps(profile).encode('utf-8'),
            },
            schema=result_list_schema,
        )
        results = response.json()['results']
        self.assertJson(
            results,
            [
                {
                    'id': 'datamart.test.basic',
                    'metadata': basic_metadata,
                    'd3m_dataset_description': basic_metadata_d3m('4.0.0'),
                    'score': lambda n: isinstance(n, float) and n > 0.0,
                    'augmentation': {
                        'left_columns': [[0]],
                        'left_columns_names': [['number']],
                        'right_columns': [[2]],
                        'right_columns_names': [['number']],
                        'type': 'join'
                    },
                    'supplied_id': None,
                    'supplied_resource_id': None
                }
            ]
        )

    def test_basic_join_only_token(self):
        with data('basic_aug.csv') as basic_aug:
            response = self.datamart_post(
                '/profile',
                files={'data': basic_aug},
            )
        token = response.json()['token']
        self.assertEqual(len(token), 40)

        response = self.datamart_post(
            '/search',
            data={'data_profile': token},
            schema=result_list_schema,
        )
        results = response.json()['results']
        self.assertJson(
            results,
            [
                {
                    'id': 'datamart.test.basic',
                    'metadata': basic_metadata,
                    'd3m_dataset_description': basic_metadata_d3m('4.0.0'),
                    'score': lambda n: isinstance(n, float) and n > 0.0,
                    'augmentation': {
                        'left_columns': [[0]],
                        'left_columns_names': [['number']],
                        'right_columns': [[2]],
                        'right_columns_names': [['number']],
                        'type': 'join'
                    },
                    'supplied_id': None,
                    'supplied_resource_id': None
                }
            ]
        )

    def test_both_data_profile(self):
        with data('basic_aug.csv') as basic_aug:
            response = self.datamart_post(
                '/profile',
                files={'data': basic_aug},
            )
            profile = response.json()

            response = self.datamart_post(
                '/search',
                files={
                    'data': basic_aug,
                    'data_profile': json.dumps(profile).encode('utf-8'),
                },
                check_status=False,
            )
            self.assertEqual(response.status_code, 400)

    def test_lazo_join(self):
        with data('lazo_aug.csv') as lazo_aug:
            response = self.datamart_post(
                '/search',
                files={
                    'data': lazo_aug,
                },
                schema=result_list_schema,
            )
        results = response.json()['results']
        self.assertJson(
            results,
            [
                {
                    'id': 'datamart.test.lazo',
                    'metadata': lazo_metadata,
                    'd3m_dataset_description': lambda d: isinstance(d, dict),
                    'score': lambda n: isinstance(n, float) and n > 0.0,
                    'augmentation': {
                        'left_columns': [[0]],
                        'left_columns_names': [['home_address']],
                        'right_columns': [[0]],
                        'right_columns_names': [['state']],
                        'type': 'join'
                    },
                    'supplied_id': None,
                    'supplied_resource_id': None
                }
            ]
        )

    def test_geo_union(self):
        query = {'keywords': ['places']}

        with data('geo_aug.csv') as geo_aug:
            response = self.datamart_post(
                '/search',
                files={
                    'query': json.dumps(query).encode('utf-8'),
                    'data': geo_aug,
                },
                schema=result_list_schema,
            )
        results = response.json()['results']
        results = [r for r in results if r['augmentation']['type'] == 'union']
        self.assertJson(
            results,
            [
                {
                    'id': 'datamart.test.geo',
                    'metadata': geo_metadata,
                    'd3m_dataset_description': geo_metadata_d3m('4.0.0'),
                    'score': lambda n: isinstance(n, float) and n > 0.0,
                    'augmentation': {
                        'left_columns': [[0], [1], [2]],
                        'left_columns_names': [['lat'], ['long'], ['id']],
                        'right_columns': [[1], [2], [0]],
                        'right_columns_names': [['lat'], ['long'], ['id']],
                        'type': 'union'
                    },
                    'supplied_id': None,
                    'supplied_resource_id': None
                }
            ]
        )

    def test_geo_union_only_data(self):
        with data('geo_aug.csv') as geo_aug:
            response = self.datamart_post(
                '/search',
                files={
                    'data': geo_aug,
                },
                schema=result_list_schema,
            )
        results = response.json()['results']
        results = [r for r in results if r['augmentation']['type'] == 'union']
        self.assertJson(
            results,
            [
                {
                    'id': 'datamart.test.geo',
                    'metadata': geo_metadata,
                    'd3m_dataset_description': geo_metadata_d3m('4.0.0'),
                    'score': lambda n: isinstance(n, float) and n > 0.0,
                    'augmentation': {
                        'left_columns': [[0], [1], [2]],
                        'left_columns_names': [['lat'], ['long'], ['id']],
                        'right_columns': [[1], [2], [0]],
                        'right_columns_names': [['lat'], ['long'], ['id']],
                        'type': 'union'
                    },
                    'supplied_id': None,
                    'supplied_resource_id': None
                }
            ]
        )

    def test_temporal_daily_join(self):
        with data('daily_aug.csv') as daily_aug:
            response = self.datamart_post(
                '/search',
                files={
                    'data': daily_aug,
                },
                schema=result_list_schema,
            )
        results = response.json()['results']
        self.assertJson(
            results,
            [
                {
                    'id': 'datamart.test.daily',
                    'metadata': daily_metadata,
                    'd3m_dataset_description':  lambda d: isinstance(d, dict),
                    'score': lambda n: isinstance(n, float) and n > 0.0,
                    'augmentation': {
                        'left_columns': [[0]],
                        'left_columns_names': [['orig_date']],
                        'right_columns': [[0]],
                        'right_columns_names':[['aug_date']],
                        'type':'join',
                    },
                    'supplied_id': None,
                    'supplied_resource_id': None,
                },
            ],
        )

    def test_temporal_hourly_join(self):
        with data('hourly_aug.csv') as hourly_aug:
            response = self.datamart_post(
                '/search',
                files={
                    'data': hourly_aug,
                },
                schema=result_list_schema,
            )
        results = response.json()['results']
        self.assertJson(
            results,
            [
                {
                    'id': 'datamart.test.hourly',
                    'metadata': hourly_metadata,
                    'd3m_dataset_description':  lambda d: isinstance(d, dict),
                    'score': lambda n: isinstance(n, float) and n > 0.0,
                    'augmentation': {
                        'left_columns': [[0]],
                        'left_columns_names': [['orig_date']],
                        'right_columns': [[0]],
                        'right_columns_names':[['aug_date']],
                        'type':'join',
                    },
                    'supplied_id': None,
                    'supplied_resource_id': None,
                },
            ],
        )

    def test_temporal_hourly_daily_join(self):
        with data('hourly_aug_days.csv') as hourly_aug_days:
            response = self.datamart_post(
                '/search',
                files={
                    'data': hourly_aug_days,
                },
                schema=result_list_schema,
            )
        results = response.json()['results']
        self.assertJson(
            results,
            [
                {
                    'id': 'datamart.test.hourly',
                    'metadata': hourly_metadata,
                    'd3m_dataset_description': lambda d: isinstance(d, dict),
                    'score': lambda n: isinstance(n, float) and n > 0.0,
                    'augmentation': {
                        'left_columns': [[0]],
                        'left_columns_names': [['orig_date']],
                        'right_columns': [[0]],
                        'right_columns_names':[['aug_date']],
                        'type':'join',
                    },
                    'supplied_id': None,
                    'supplied_resource_id': None,
                },
            ],
        )


class TestDownload(DatamartTest):
    def test_get_id(self):
        """Download datasets via GET /download/{dataset_id}"""
        # Basic dataset, materialized via direct_url
        response = self.datamart_get('/download/' + 'datamart.test.basic',
                                     # format defaults to csv
                                     allow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'],
                         'http://test_discoverer:7000/basic.csv')

        response = self.datamart_get('/download/' + 'datamart.test.basic',
                                     # explicit format
                                     params={'format': 'csv'},
                                     allow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'],
                         'http://test_discoverer:7000/basic.csv')

        response = self.datamart_get('/download/' + 'datamart.test.basic',
                                     params={'format': 'd3m'},
                                     allow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertEqual(set(zip_.namelist()),
                         {'datasetDoc.json', 'tables/learningData.csv'})
        self.assertEqual(
            json.load(zip_.open('datasetDoc.json')),
            basic_metadata_d3m('4.0.0'),
        )

        response = self.datamart_get(
            '/download/' + 'datamart.test.basic',
            params={'format': 'd3m', 'format_version': '3.2.0'},
            allow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertEqual(set(zip_.namelist()),
                         {'datasetDoc.json', 'tables/learningData.csv'})
        self.assertEqual(
            json.load(zip_.open('datasetDoc.json')),
            basic_metadata_d3m('3.2.0'),
        )

        # Geo dataset, materialized via /datasets storage
        response = self.datamart_get('/download/' + 'datamart.test.geo',
                                     # format defaults to csv
                                     allow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'],
                         'application/octet-stream')
        self.assertTrue(response.content.startswith(b'id,lat,long,height\n'))

    def test_post(self):
        """Download datasets via POST /download"""
        # Basic dataset, materialized via direct_url
        basic_meta = self.datamart_get(
            '/metadata/' + 'datamart.test.basic'
        )
        basic_meta = basic_meta.json()['metadata']

        response = self.datamart_post(
            '/download', allow_redirects=False,
            params={'format': 'd3m', 'format_version': '3.2.0'},
            files={'task': json.dumps(
                {
                    'id': 'datamart.test.basic',
                    'score': 1.0,
                    'metadata': basic_meta
                }
            ).encode('utf-8')},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertEqual(set(zip_.namelist()),
                         {'datasetDoc.json', 'tables/learningData.csv'})
        self.assertEqual(
            json.load(zip_.open('datasetDoc.json')),
            basic_metadata_d3m('3.2.0'),
        )

        response = self.datamart_post(
            '/download', allow_redirects=False,
            params={'format': 'csv'},
            json={
                'id': 'datamart.test.basic',
                'score': 1.0,
                'metadata': basic_meta
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'],
                         'http://test_discoverer:7000/basic.csv')

        # Geo dataset, materialized via /datasets storage
        geo_meta = self.datamart_get(
            '/metadata/' + 'datamart.test.geo'
        )
        geo_meta = geo_meta.json()['metadata']

        response = self.datamart_post(
            '/download', allow_redirects=False,
            # format defaults to csv
            files={'task': json.dumps(
                {
                    'id': 'datamart.test.geo',
                    'score': 1.0,
                    'metadata': geo_meta
                }
            ).encode('utf-8')},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'],
                         'application/octet-stream')
        self.assertTrue(response.content.startswith(b'id,lat,long,height\n'))

        response = self.datamart_post(
            '/download', allow_redirects=False,
            params={'format': 'd3m'},
            json={
                'id': 'datamart.test.geo',
                'score': 1.0,
                'metadata': geo_meta
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertEqual(set(zip_.namelist()),
                         {'datasetDoc.json', 'tables/learningData.csv'})
        self.assertEqual(
            json.load(zip_.open('datasetDoc.json')),
            geo_metadata_d3m('4.0.0'),
        )

    def test_post_invalid(self):
        """Post invalid materialization information."""
        response = self.datamart_post(
            '/download', allow_redirects=False,
            files={'task': json.dumps(
                {
                    'id': 'datamart.nonexistent',
                    'score': 0.0,
                    'metadata': {
                        'name': "Non-existent dataset",
                        'materialize': {
                            'identifier': 'datamart.nonexistent',
                        }
                    }
                }
            ).encode('utf-8')},
            check_status=False,
        )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {'error': "Materializer reports failure"},
        )

        response = self.datamart_post(
            '/download', allow_redirects=False,
            files={},
            check_status=False,
        )
        self.assertEqual(response.status_code, 400)

    def test_get_id_invalid(self):
        response = self.datamart_get(
            '/download/datamart.nonexistent',
            check_status=False,
        )
        self.assertEqual(response.status_code, 404)

        response = self.datamart_get(
            '/metadata/datamart.nonexistent',
            check_status=False,
        )
        self.assertEqual(response.status_code, 404)

    def test_materialize(self):
        """Test datamart_materialize."""
        def assert_same_files(a, b):
            with open(a, 'r') as f_a:
                with open(b, 'r') as f_b:
                    self.assertEqual(f_a.read(), f_b.read())

        with tempfile.TemporaryDirectory() as tempdir:
            df = datamart_materialize.download(
                'datamart.test.agg',
                None,
                os.environ['QUERY_HOST'],
                'pandas',
            )
            self.assertEqual(df.shape, (8, 3))

            datamart_materialize.download(
                'datamart.test.geo',
                os.path.join(tempdir, 'geo.csv'),
                os.environ['QUERY_HOST'],
            )
            assert_same_files(
                os.path.join(tempdir, 'geo.csv'),
                os.path.join(os.path.dirname(__file__), 'data/geo.csv'),
            )

            datamart_materialize.download(
                'datamart.test.agg',
                os.path.join(tempdir, 'agg'),
                os.environ['QUERY_HOST'],
                'd3m',
            )
            assert_same_files(
                os.path.join(tempdir, 'agg/tables/learningData.csv'),
                os.path.join(os.path.dirname(__file__), 'data/agg.csv'),
            )

    def test_basic_add_index(self):
        """Test adding d3mIndex automatically."""
        response = self.datamart_get(
            '/download/' + 'datamart.test.basic',
            params={'format': 'd3m',  'format_need_d3mindex': '1'},
            allow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertEqual(set(zip_.namelist()),
                         {'datasetDoc.json', 'tables/learningData.csv'})
        meta = basic_metadata_d3m('4.0.0')
        index_meta = {
            'colIndex': 0,
            'colName': 'd3mIndex',
            'colType': 'integer',
            'role': ['index'],
        }
        meta['dataResources'][0]['columns'] = [index_meta] + [
            dict(col, colIndex=col['colIndex'] + 1)
            for col in meta['dataResources'][0]['columns']
        ]
        self.assertEqual(
            json.load(zip_.open('datasetDoc.json')),
            meta,
        )
        with data('basic.d3m.csv') as f_ref:
            self.assertEqual(
                zip_.open('tables/learningData.csv').read(),
                f_ref.read(),
            )


class TestAugment(DatamartTest):
    def test_basic_join(self):
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.basic'
        )
        meta = meta.json()['metadata']

        task = {
            'id': 'datamart.test.basic',
            'metadata': meta,
            'score': 1.0,
            'augmentation': {
                'left_columns': [[0]],
                'left_columns_names': [['number']],
                'right_columns': [[2]],
                'right_columns_names': [['number']],
                'type': 'join'
            },
            'supplied_id': None,
            'supplied_resource_id': None
        }

        with data('basic_aug.csv') as basic_aug:
            response = self.datamart_post(
                '/augment',
                files={
                    'task': json.dumps(task).encode('utf-8'),
                    'data': basic_aug,
                },
            )
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        self.assertTrue(
            response.headers['Content-Disposition'].startswith('attachment')
        )
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        zip_.testzip()
        self.assertEqual(
            set(zip_.namelist()),
            {'datasetDoc.json', 'tables/learningData.csv'},
        )
        with zip_.open('tables/learningData.csv') as table:
            self.assertCsvEqualNoOrder(
                table.read().decode('utf-8'),
                'number,desk_faces,name,country,what',
                [
                    '5,west,james,canada,False',
                    '4,south,john,usa,False',
                    '7,west,michael,usa,True',
                    '6,east,robert,usa,False',
                    '11,,christopher,canada,True',
                ],
            )
        with zip_.open('datasetDoc.json') as meta_fp:
            meta = json.load(meta_fp)
            self.assertJson(
                meta,
                {
                    'about': {
                        'approximateSize': '161 B',
                        'datasetID': lambda s: len(s) == 32,
                        'datasetName': lambda s: len(s) == 32,
                        'datasetSchemaVersion': '4.0.0',
                        'datasetVersion': '1.0',
                        'license': 'unknown',
                        'redacted': False,
                    },
                    'dataResources': [
                        {
                            'columns': [
                                {
                                    'colIndex': 0,
                                    'colName': 'number',
                                    'colType': 'integer',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 1,
                                    'colName': 'desk_faces',
                                    'colType': 'string',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 2,
                                    'colName': 'name',
                                    'colType': 'string',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 3,
                                    'colName': 'country',
                                    'colType': 'categorical',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 4,
                                    'colName': 'what',
                                    'colType': 'boolean',
                                    'role': ['attribute'],
                                },
                            ],
                            'isCollection': False,
                            'resFormat': {'text/csv': ["csv"]},
                            'resID': 'learningData',
                            'resPath': 'tables/learningData.csv',
                            'resType': 'table',
                        },
                    ],
                    'qualities': [
                        {
                            'qualName': 'augmentation_info',
                            'qualValue': {
                                'augmentation_type': 'join',
                                'nb_rows_after': 5,
                                'nb_rows_before': 5,
                                'new_columns': ['name', 'country', 'what'],
                                'removed_columns': [],
                            },
                            'qualValueType': 'dict',
                        },
                    ],
                },
            )

    def test_basic_join_auto(self):
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.basic'
        )
        meta = meta.json()['metadata']

        task = {
            'id': 'datamart.test.basic',
            'metadata': meta,
            'score': 1.0,
            'augmentation': {
                'type': 'none'
            },
            'supplied_id': None,
            'supplied_resource_id': None
        }

        with data('basic_aug.csv') as basic_aug:
            response = self.datamart_post(
                '/augment',
                files={
                    'task': json.dumps(task).encode('utf-8'),
                    'data': basic_aug,
                },
            )
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        self.assertTrue(
            response.headers['Content-Disposition'].startswith('attachment')
        )
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        zip_.testzip()
        self.assertEqual(
            set(zip_.namelist()),
            {'datasetDoc.json', 'tables/learningData.csv'},
        )
        with zip_.open('tables/learningData.csv') as table:
            self.assertCsvEqualNoOrder(
                table.read().decode('utf-8'),
                'number,desk_faces,name,country,what',
                [
                    '5,west,james,canada,False',
                    '4,south,john,usa,False',
                    '7,west,michael,usa,True',
                    '6,east,robert,usa,False',
                    '11,,christopher,canada,True',
                ],
            )
        with zip_.open('datasetDoc.json') as meta_fp:
            meta = json.load(meta_fp)
            self.assertJson(
                meta,
                {
                    'about': {
                        'approximateSize': '161 B',
                        'datasetID': lambda s: len(s) == 32,
                        'datasetName': lambda s: len(s) == 32,
                        'datasetSchemaVersion': '4.0.0',
                        'datasetVersion': '1.0',
                        'license': 'unknown',
                        'redacted': False,
                    },
                    'dataResources': [
                        {
                            'columns': [
                                {
                                    'colIndex': 0,
                                    'colName': 'number',
                                    'colType': 'integer',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 1,
                                    'colName': 'desk_faces',
                                    'colType': 'string',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 2,
                                    'colName': 'name',
                                    'colType': 'string',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 3,
                                    'colName': 'country',
                                    'colType': 'categorical',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 4,
                                    'colName': 'what',
                                    'colType': 'boolean',
                                    'role': ['attribute'],
                                },
                            ],
                            'isCollection': False,
                            'resFormat': {'text/csv': ["csv"]},
                            'resID': 'learningData',
                            'resPath': 'tables/learningData.csv',
                            'resType': 'table',
                        },
                    ],
                    'qualities': [
                        {
                            'qualName': 'augmentation_info',
                            'qualValue': {
                                'augmentation_type': 'join',
                                'nb_rows_after': 5,
                                'nb_rows_before': 5,
                                'new_columns': ['name', 'country', 'what'],
                                'removed_columns': [],
                            },
                            'qualValueType': 'dict',
                        },
                    ],
                },
            )

    def test_agg_join(self):
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.agg'
        )
        meta = meta.json()['metadata']

        task = {
            'id': 'datamart.test.agg',
            'metadata': meta,
            'score': 1.0,
            'augmentation': {
                'left_columns': [[0]],
                'left_columns_names': [['id']],
                'right_columns': [[0]],
                'right_columns_names': [['id']],
                'type': 'join'
            },
            'supplied_id': None,
            'supplied_resource_id': None
        }

        with data('agg_aug.csv') as agg_aug:
            response = self.datamart_post(
                '/augment',
                files={
                    'task': json.dumps(task).encode('utf-8'),
                    'data': agg_aug,
                },
            )
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        self.assertTrue(
            response.headers['Content-Disposition'].startswith('attachment')
        )
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        zip_.testzip()
        self.assertEqual(
            set(zip_.namelist()),
            {'datasetDoc.json', 'tables/learningData.csv'},
        )
        with zip_.open('tables/learningData.csv') as table:
            self.assertCsvEqualNoOrder(
                table.read().decode('utf-8'),
                'id,location,work,mean salary,sum salary,max salary,min salary',
                [
                    '30,korea,True,150.0,300.0,200.0,100.0',
                    '40,brazil,False,,,,',
                    '70,usa,True,600.0,600.0,600.0,600.0',
                    '80,canada,True,200.0,200.0,200.0,200.0',
                    '100,france,False,250.0,500.0,300.0,200.0',
                ],
            )
        with zip_.open('datasetDoc.json') as meta_fp:
            meta = json.load(meta_fp)
            self.assertJson(
                meta,
                {
                    'about': {
                        'approximateSize': '236 B',
                        'datasetID': lambda s: len(s) == 32,
                        'datasetName': lambda s: len(s) == 32,
                        'datasetSchemaVersion': '4.0.0',
                        'datasetVersion': '1.0',
                        'license': 'unknown',
                        'redacted': False,
                    },
                    'dataResources': [
                        {
                            'columns': [
                                {
                                    'colIndex': 0,
                                    'colName': 'id',
                                    'colType': 'integer',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 1,
                                    'colName': 'location',
                                    'colType': 'string',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 2,
                                    'colName': 'work',
                                    'colType': 'boolean',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 3,
                                    'colName': 'mean salary',
                                    'colType': 'real',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 4,
                                    'colName': 'sum salary',
                                    'colType': 'real',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 5,
                                    'colName': 'max salary',
                                    'colType': 'real',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 6,
                                    'colName': 'min salary',
                                    'colType': 'real',
                                    'role': ['attribute'],
                                },
                            ],
                            'isCollection': False,
                            'resFormat': {'text/csv': ["csv"]},
                            'resID': 'learningData',
                            'resPath': 'tables/learningData.csv',
                            'resType': 'table',
                        },
                    ],
                    'qualities': [
                        {
                            'qualName': 'augmentation_info',
                            'qualValue': {
                                'augmentation_type': 'join',
                                'nb_rows_after': 5,
                                'nb_rows_before': 5,
                                'new_columns': [
                                    'work', 'mean salary', 'sum salary',
                                    'max salary', 'min salary',
                                ],
                                'removed_columns': [],
                            },
                            'qualValueType': 'dict',
                        },
                    ],
                },
            )

    def test_agg_join_specific_functions(self):
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.agg'
        )
        meta = meta.json()['metadata']

        task = {
            'id': 'datamart.test.agg',
            'metadata': meta,
            'score': 1.0,
            'augmentation': {
                'left_columns': [[0]],
                'left_columns_names': [['id']],
                'right_columns': [[0]],
                'right_columns_names': [['id']],
                'type': 'join',
                'agg_functions': {
                    'work': 'count',
                    'salary': ['first', 'sum', 'max'],
                }
            },
            'supplied_id': None,
            'supplied_resource_id': None
        }

        with data('agg_aug.csv') as agg_aug:
            response = self.datamart_post(
                '/augment',
                files={
                    'task': json.dumps(task).encode('utf-8'),
                    'data': agg_aug,
                },
            )
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        self.assertTrue(
            response.headers['Content-Disposition'].startswith('attachment')
        )
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        zip_.testzip()
        self.assertEqual(
            set(zip_.namelist()),
            {'datasetDoc.json', 'tables/learningData.csv'},
        )
        with zip_.open('tables/learningData.csv') as table:
            self.assertCsvEqualNoOrder(
                table.read().decode('utf-8'),
                'id,location,count work,first salary,sum salary,max salary',
                [
                    '30,korea,2,200.0,300.0,200.0',
                    '40,brazil,1,,,',
                    '70,usa,2,,600.0,600.0',
                    '80,canada,1,200.0,200.0,200.0',
                    '100,france,2,300.0,500.0,300.0',
                ],
            )
        with zip_.open('datasetDoc.json') as meta_fp:
            meta = json.load(meta_fp)
            self.assertJson(
                meta,
                {
                    'about': {
                        'approximateSize': '185 B',
                        'datasetID': lambda s: len(s) == 32,
                        'datasetName': lambda s: len(s) == 32,
                        'datasetSchemaVersion': '4.0.0',
                        'datasetVersion': '1.0',
                        'license': 'unknown',
                        'redacted': False,
                    },
                    'dataResources': [
                        {
                            'columns': [
                                {
                                    'colIndex': 0,
                                    'colName': 'id',
                                    'colType': 'integer',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 1,
                                    'colName': 'location',
                                    'colType': 'string',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 2,
                                    'colName': 'count work',
                                    'colType': 'boolean',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 3,
                                    'colName': 'first salary',
                                    'colType': 'integer',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 4,
                                    'colName': 'sum salary',
                                    'colType': 'real',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 5,
                                    'colName': 'max salary',
                                    'colType': 'real',
                                    'role': ['attribute'],
                                },
                            ],
                            'isCollection': False,
                            'resFormat': {'text/csv': ["csv"]},
                            'resID': 'learningData',
                            'resPath': 'tables/learningData.csv',
                            'resType': 'table',
                        },
                    ],
                    'qualities': [
                        {
                            'qualName': 'augmentation_info',
                            'qualValue': {
                                'augmentation_type': 'join',
                                'nb_rows_after': 5,
                                'nb_rows_before': 5,
                                'new_columns': [
                                    'count work', 'first salary',
                                    'sum salary', 'max salary',
                                ],
                                'removed_columns': [],
                            },
                            'qualValueType': 'dict',
                        },
                    ],
                },
            )

    def test_lazo_join(self):
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.lazo'
        )
        meta = meta.json()['metadata']

        task = {
            'id': 'datamart.test.lazo',
            'metadata': meta,
            'score': 1.0,
            'augmentation': {
                'left_columns': [[0]],
                'left_columns_names': [['home_address']],
                'right_columns': [[0]],
                'right_columns_names': [['state']],
                'type': 'join'
            },
            'supplied_id': None,
            'supplied_resource_id': None
        }

        with data('lazo_aug.csv') as lazo_aug:
            response = self.datamart_post(
                '/augment',
                files={
                    'task': json.dumps(task).encode('utf-8'),
                    'data': lazo_aug,
                },
            )
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        self.assertTrue(
            response.headers['Content-Disposition'].startswith('attachment')
        )
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        zip_.testzip()
        self.assertEqual(
            set(zip_.namelist()),
            {'datasetDoc.json', 'tables/learningData.csv'},
        )
        with zip_.open('tables/learningData.csv') as table:
            self.assertCsvEqualNoOrder(
                table.read().decode('utf-8'),
                'home_address,mean year,sum year,max year,min year',
                [
                    'AZ,1990.0,1990.0,1990.0,1990.0',
                    'PA,1990.0,1990.0,1990.0,1990.0',
                    'SD,,,,',
                    'NJ,1990.0,1990.0,1990.0,1990.0',
                    'NH,,,,',
                    'TX,1990.0,1990.0,1990.0,1990.0',
                    'MS,1990.0,1990.0,1990.0,1990.0',
                    'TN,1990.0,1990.0,1990.0,1990.0',
                    'WA,1990.0,1990.0,1990.0,1990.0',
                    'VA,1990.0,1990.0,1990.0,1990.0',
                    'NY,1990.0,1990.0,1990.0,1990.0',
                    'OH,1990.0,1990.0,1990.0,1990.0',
                    'OR,1990.0,1990.0,1990.0,1990.0',
                    'IL,1990.0,1990.0,1990.0,1990.0',
                    'MT,,,,',
                    'GA,1990.0,1990.0,1990.0,1990.0',
                    'FL,,,,',
                    'HI,,,,',
                    'CA,1990.0,1990.0,1990.0,1990.0',
                    'NC,1990.0,1990.0,1990.0,1990.0',
                    'UT,1991.0,1991.0,1991.0,1991.0',
                    'SC,1991.0,1991.0,1991.0,1991.0',
                    'LA,1990.0,1990.0,1990.0,1990.0',
                    'RI,,,,',
                    'PR,1990.0,1990.0,1990.0,1990.0',
                    'DE,,,,',
                ],
            )
        with zip_.open('datasetDoc.json') as meta_fp:
            meta = json.load(meta_fp)
            self.assertJson(
                meta,
                {
                    'about': {
                        'approximateSize': '688 B',
                        'datasetID': lambda s: len(s) == 32,
                        'datasetName': lambda s: len(s) == 32,
                        'datasetSchemaVersion': '4.0.0',
                        'datasetVersion': '1.0',
                        'license': 'unknown',
                        'redacted': False,
                    },
                    'dataResources': [
                        {
                            'columns': [
                                {
                                    'colIndex': 0,
                                    'colName': 'home_address',
                                    'colType': 'string',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 1,
                                    'colName': 'mean year',
                                    'colType': 'real',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 2,
                                    'colName': 'sum year',
                                    'colType': 'real',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 3,
                                    'colName': 'max year',
                                    'colType': 'real',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 4,
                                    'colName': 'min year',
                                    'colType': 'real',
                                    'role': ['attribute'],
                                },
                            ],
                            'isCollection': False,
                            'resFormat': {'text/csv': ["csv"]},
                            'resID': 'learningData',
                            'resPath': 'tables/learningData.csv',
                            'resType': 'table',
                        },
                    ],
                    'qualities': [
                        {
                            'qualName': 'augmentation_info',
                            'qualValue': {
                                'augmentation_type': 'join',
                                'nb_rows_after': 26, 'nb_rows_before': 26,
                                'new_columns': [
                                    'mean year', 'sum year',
                                    'max year', 'min year',
                                ],
                                'removed_columns': [],
                            },
                            'qualValueType': 'dict',
                        },
                    ],
                },
            )

    def test_geo_union(self):
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.geo'
        )
        meta = meta.json()['metadata']

        task = {
            'id': 'datamart.test.geo',
            'metadata': meta,
            'score': 1.0,
            'augmentation': {
                'left_columns': [[0], [1], [2]],
                'left_columns_names': [['lat'], ['long'], ['id']],
                'right_columns': [[1], [2], [0]],
                'right_columns_names': [['lat'], ['long'], ['id']],
                'type': 'union'
            },
            'supplied_id': None,
            'supplied_resource_id': None
        }

        with data('geo_aug.csv') as geo_aug:
            response = self.datamart_post(
                '/augment',
                files={
                    'task': json.dumps(task).encode('utf-8'),
                    'data': geo_aug,
                },
            )
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        self.assertTrue(
            response.headers['Content-Disposition'].startswith('attachment')
        )
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        zip_.testzip()
        self.assertEqual(
            set(zip_.namelist()),
            {'datasetDoc.json', 'tables/learningData.csv'},
        )
        with zip_.open('tables/learningData.csv') as table:
            table_lines = table.read().decode('utf-8').splitlines(False)
            # Truncate fields to work around rounding errors
            # FIXME: Deal with rounding errors
            table_lines = [
                ','.join(e[:8] for e in l.split(','))
                for l in table_lines
            ]
            self.assertCsvEqualNoOrder(
                '\n'.join(table_lines[0:6]),
                'lat,long,id,letter',
                [
                    '40.73279,-73.9985,place100,a',
                    '40.72970,-73.9978,place101,b',
                    '40.73266,-73.9975,place102,c',
                    '40.73117,-74.0018,place103,d',
                    '40.69427,-73.9898,place104,e',
                ],
            )
        with zip_.open('datasetDoc.json') as meta_fp:
            meta = json.load(meta_fp)
            self.assertJson(
                meta,
                {
                    'about': {
                        'approximateSize': '3688 B',
                        'datasetID': lambda s: len(s) == 32,
                        'datasetName': lambda s: len(s) == 32,
                        'datasetSchemaVersion': '4.0.0',
                        'datasetVersion': '1.0',
                        'license': 'unknown',
                        'redacted': False,
                    },
                    'dataResources': [
                        {
                            'columns': [
                                {
                                    'colIndex': 0,
                                    'colName': 'lat',
                                    'colType': 'real',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 1,
                                    'colName': 'long',
                                    'colType': 'real',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 2,
                                    'colName': 'id',
                                    'colType': 'string',
                                    'role': ['attribute'],
                                },
                                {
                                    'colIndex': 3,
                                    'colName': 'letter',
                                    'colType': 'string',
                                    'role': ['attribute'],
                                },
                            ],
                            'isCollection': False,
                            'resFormat': {'text/csv': ["csv"]},
                            'resID': 'learningData',
                            'resPath': 'tables/learningData.csv',
                            'resType': 'table',
                        },
                    ],
                    'qualities': [
                        {
                            'qualName': 'augmentation_info',
                            'qualValue': {
                                'augmentation_type': 'union',
                                'nb_rows_after': 110,
                                'nb_rows_before': 10,
                                'new_columns': [],
                                'removed_columns': [],
                            },
                            'qualValueType': 'dict',
                        },
                    ],
                },
            )

    def test_temporal_daily_join(self):
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.daily'
        )
        meta = meta.json()['metadata']

        task = {
            'id': 'datamart.test.daily',
            'metadata': meta,
            'score': 1.0,
            'augmentation': {
                'left_columns': [[0]],
                'left_columns_names': [['orig_date']],
                'right_columns': [[0]],
                'right_columns_names': [['aug_date']],
                'type': 'join'
            },
            'supplied_id': None,
            'supplied_resource_id': None
        }

        with data('daily_aug.csv') as daily_aug:
            response = self.datamart_post(
                '/augment',
                files={
                    'task': json.dumps(task).encode('utf-8'),
                    'data': daily_aug,
                },
            )
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        self.assertTrue(
            response.headers['Content-Disposition'].startswith('attachment')
        )
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        zip_.testzip()
        self.assertEqual(
            set(zip_.namelist()),
            {'datasetDoc.json', 'tables/learningData.csv'},
        )
        with zip_.open('tables/learningData.csv') as table:
            self.assertCsvEqualNoOrder(
                table.read().decode('utf-8'),
                'orig_date,n_people,rain',
                [
                    '2019-04-28,3,yes',
                    '2019-04-29,5,yes',
                    '2019-04-30,0,yes',
                    '2019-05-01,1,no',
                    '2019-05-02,3,no',
                    '2019-05-03,2,yes',
                ],
            )

    def test_temporal_hourly_join(self):
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.hourly'
        )
        meta = meta.json()['metadata']

        task = {
            'id': 'datamart.test.hourly',
            'metadata': meta,
            'score': 1.0,
            'augmentation': {
                'left_columns': [[0]],
                'left_columns_names': [['orig_date']],
                'right_columns': [[0]],
                'right_columns_names': [['aug_date']],
                'type': 'join'
            },
            'supplied_id': None,
            'supplied_resource_id': None
        }

        with data('hourly_aug.csv') as hourly_aug:
            response = self.datamart_post(
                '/augment',
                files={
                    'task': json.dumps(task).encode('utf-8'),
                    'data': hourly_aug,
                },
            )
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        self.assertTrue(
            response.headers['Content-Disposition'].startswith('attachment')
        )
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        zip_.testzip()
        self.assertEqual(
            set(zip_.namelist()),
            {'datasetDoc.json', 'tables/learningData.csv'},
        )
        with zip_.open('tables/learningData.csv') as table:
            self.assertCsvEqualNoOrder(
                table.read().decode('utf-8'),
                'orig_date,color,rain',
                [
                    '2019-06-13T01:00:00,blue,no',
                    '2019-06-13T02:00:00,blue,no',
                    '2019-06-13T03:00:00,green,no',
                    '2019-06-13T04:00:00,green,yes',
                    '2019-06-13T05:00:00,blue,no',
                ],
            )

    def test_temporal_hourly_days_join(self):
        """Join daily data with hourly (= aggregate down to daily)."""
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.hourly'
        )
        meta = meta.json()['metadata']

        task = {
            'id': 'datamart.test.hourly',
            'metadata': meta,
            'score': 1.0,
            'augmentation': {
                'left_columns': [[0]],
                'left_columns_names': [['orig_date']],
                'right_columns': [[0]],
                'right_columns_names': [['aug_date']],
                'type': 'join'
            },
            'supplied_id': None,
            'supplied_resource_id': None
        }

        with data('hourly_aug_days.csv') as hourly_aug_days:
            response = self.datamart_post(
                '/augment',
                files={
                    'task': json.dumps(task).encode('utf-8'),
                    'data': hourly_aug_days,
                },
            )
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        self.assertTrue(
            response.headers['Content-Disposition'].startswith('attachment')
        )
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        zip_.testzip()
        self.assertEqual(
            set(zip_.namelist()),
            {'datasetDoc.json', 'tables/learningData.csv'},
        )
        with zip_.open('tables/learningData.csv') as table:
            self.assertCsvEqualNoOrder(
                table.read().decode('utf-8'),
                'orig_date,color,rain',
                [
                    '2019-06-12,blue,no',
                    '2019-06-13,green,no',
                ],
            )

    def test_temporal_daily_hours_join(self):
        """Join hourly data with daily (= repeat for each hour)."""
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.daily'
        )
        meta = meta.json()['metadata']

        task = {
            'id': 'datamart.test.daily',
            'metadata': meta,
            'score': 1.0,
            'augmentation': {
                'left_columns': [[0]],
                'left_columns_names': [['orig_date']],
                'right_columns': [[0]],
                'right_columns_names': [['aug_date']],
                'type': 'join'
            },
            'supplied_id': None,
            'supplied_resource_id': None
        }

        with data('daily_aug_hours.csv') as daily_aug_hours:
            response = self.datamart_post(
                '/augment',
                files={
                    'task': json.dumps(task).encode('utf-8'),
                    'data': daily_aug_hours,
                },
            )
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        self.assertTrue(
            response.headers['Content-Disposition'].startswith('attachment')
        )
        zip_ = zipfile.ZipFile(io.BytesIO(response.content))
        zip_.testzip()
        self.assertEqual(
            set(zip_.namelist()),
            {'datasetDoc.json', 'tables/learningData.csv'},
        )
        with zip_.open('tables/learningData.csv') as table:
            self.assertCsvEqualNoOrder(
                table.read().decode('utf-8'),
                'orig_date,n_people,rain',
                [
                    '2019-04-25T21:00:00Z,3,yes',
                    '2019-04-26T01:00:00Z,5,no',
                    '2019-04-26T05:00:00Z,6,no',
                    '2019-04-26T09:00:00Z,7,no',
                    '2019-04-26T13:00:00Z,6,no',
                    '2019-04-26T17:00:00Z,8,no',
                    '2019-04-26T21:00:00Z,7,no',
                    '2019-04-27T01:00:00Z,0,yes',
                    '2019-04-27T05:00:00Z,1,yes',
                    '2019-04-27T09:00:00Z,0,yes',
                    '2019-04-27T13:00:00Z,3,yes',
                    '2019-04-27T17:00:00Z,0,yes',
                    '2019-04-27T13:00:00Z,0,yes',
                ],
            )


version = os.environ['DATAMART_VERSION']
assert re.match(r'^v[0-9]+(\.[0-9]+)+(-[0-9]+-g[0-9a-f]{7})?$', version)


basic_metadata = {
    "id": "datamart.test.basic",
    "name": "basic",
    "description": "This is a very simple CSV with people",
    'source': 'remi',
    "size": 425,
    "nb_rows": 20,
    "nb_profiled_rows": 20,
    "columns": [
        {
            "name": "name",
            "structural_type": "http://schema.org/Text",
            "semantic_types": [],
            "num_distinct_values": 20
        },
        {
            "name": "country",
            "structural_type": "http://schema.org/Text",
            "semantic_types": ["http://schema.org/Enumeration"],
            "num_distinct_values": 2,
            "plot": check_plot('histogram_categorical'),
        },
        {
            "name": "number",
            "structural_type": "http://schema.org/Integer",
            "semantic_types": [],
            "mean": lambda n: round(n, 3) == 6.150,
            "stddev": lambda n: round(n, 3) == 1.526,
            "coverage": (
                lambda l: sorted(l, key=lambda e: e['range']['gte']) == [
                    {
                        "range": {
                            "gte": 4.0,
                            "lte": 5.0
                        }
                    },
                    {
                        "range": {
                            "gte": 6.0,
                            "lte": 7.0
                        }
                    },
                    {
                        "range": {
                            "gte": 11.0,
                            "lte": 11.0
                        }
                    }
                ]
            ),
            "plot": check_plot('histogram_numerical'),
        },
        {
            "name": "what",
            "structural_type": "http://schema.org/Text",
            "semantic_types": [
                "http://schema.org/Boolean",
                "http://schema.org/Enumeration"
            ],
            "unclean_values_ratio": 0.0,
            "num_distinct_values": 2,
            "plot": check_plot('histogram_categorical'),
        }
    ],
    "materialize": {
        "direct_url": "http://test_discoverer:7000/basic.csv",
        "identifier": "datamart.test",
        "date": lambda d: isinstance(d, str)
    },
    "sample": "name,country,number,what\njames,canada,5,false\n" +
              "john,usa,4,false\nrobert,usa,6,false\nmichael,usa,7,true\n" +
              "william,usa,7,true\ndavid,canada,5,false\n" +
              "richard,canada,7,true\njoseph,usa,6,true\n" +
              "thomas,usa,6,false\ncharles,usa,7,false\n" +
              "christopher,canada,11,true\ndaniel,usa,5,false\n"
              "matthew,canada,7,true\nanthony,canada,7,true\n" +
              "donald,usa,6,true\nmark,usa,4,false\npaul,usa,4,false\n" +
              "steven,usa,6,false\nandrew,canada,6,false\n" +
              "kenneth,canada,7,true\n",
    "date": lambda d: isinstance(d, str),
    "version": version
}


basic_metadata_d3m = lambda v: {
    'about': {
        'datasetID': 'datamart.test.basic',
        'datasetName': 'basic',
        'description': 'This is a very simple CSV with people',
        'license': 'unknown',
        'approximateSize': '425 B',
        'datasetSchemaVersion': v,
        'redacted': False,
        'datasetVersion': '1.0',
    },
    'dataResources': [
        {
            'resID': 'learningData',
            'resPath': 'tables/learningData.csv',
            'resType': 'table',
            'resFormat': ({'text/csv': ["csv"]} if v == '4.0.0'
                          else ['text/csv']),
            'isCollection': False,
            'columns': [
                {
                    'colIndex': 0,
                    'colName': 'name',
                    'colType': 'string',
                    'role': ['attribute'],
                },
                {
                    'colIndex': 1,
                    'colName': 'country',
                    'colType': 'categorical',
                    'role': ['attribute'],
                },
                {
                    'colIndex': 2,
                    'colName': 'number',
                    'colType': 'integer',
                    'role': ['attribute'],
                },
                {
                    'colIndex': 3,
                    'colName': 'what',
                    'colType': 'boolean',
                    'role': ['attribute'],
                },
            ],
        },
    ],
}


agg_metadata = {
    "id": "datamart.test.agg",
    "name": "agg",
    "description": "Simple CSV with ids and salaries to test aggregation for numerical attributes",
    'source': 'fernando',
    "size": 110,
    "nb_rows": 8,
    "nb_profiled_rows": 8,
    "columns": [
        {
            "name": "id",
            "structural_type": "http://schema.org/Integer",
            "semantic_types": [
                "http://schema.org/identifier"
            ],
            "mean": 65.0,
            "stddev": lambda n: round(n, 3) == 26.926,
            "coverage": (
                lambda l: sorted(l, key=lambda e: e['range']['gte']) == [
                    {
                        "range": {
                            "gte": 30.0,
                            "lte": 40.0
                        }
                    },
                    {
                        "range": {
                            "gte": 70.0,
                            "lte": 80.0
                        }
                    },
                    {
                        "range": {
                            "gte": 100.0,
                            "lte": 100.0
                        }
                    }
                ]
            ),
            "plot": check_plot('histogram_numerical'),
        },
        {
            "name": "work",
            "structural_type": "http://schema.org/Text",
            "semantic_types": [
                "http://schema.org/Boolean",
                'http://schema.org/Enumeration',
            ],
            "unclean_values_ratio": 0.0,
            "num_distinct_values": 2,
            "plot": check_plot('histogram_categorical'),
        },
        {
            "name": "salary",
            "structural_type": "http://schema.org/Integer",
            "semantic_types": [],
            'missing_values_ratio': 0.25,
            "mean": 200.0,
            "stddev": lambda n: round(n, 3) == 150.0,
            "coverage": (
                lambda l: sorted(l, key=lambda e: e['range']['gte']) == [
                    {
                        "range": {
                            "gte": 100.0,
                            "lte": 100.0
                        }
                    },
                    {
                        "range": {
                            "gte": 200.0,
                            "lte": 300.0
                        }
                    },
                    {
                        "range": {
                            "gte": 600.0,
                            "lte": 600.0
                        }
                    }
                ]
            ),
            "plot": check_plot('histogram_numerical'),
        }
    ],
    "materialize": {
        "identifier": "datamart.test",
        "date": lambda d: isinstance(d, str)
    },
    "sample": "id,work,salary\n40,false,\n30,true,200\n70,true,\n80,true,200" +
              "\n100,false,300\n100,true,200\n30,false,100\n70,false,600\n",
    "date": lambda d: isinstance(d, str),
    "version": version
}


geo_metadata = {
    "id": "datamart.test.geo",
    "name": "geo",
    "description": "Another simple CSV with places",
    'source': 'remi',
    "size": 3910,
    "nb_rows": 100,
    "nb_profiled_rows": 100,
    "columns": [
        {
            "name": "id",
            "structural_type": "http://schema.org/Text",
            "semantic_types": [],
            "missing_values_ratio": 0.01,
            "num_distinct_values": 99
        },
        {
            "name": "lat",
            "structural_type": "http://schema.org/Float",
            "semantic_types": lambda l: "http://schema.org/latitude" in l,
            "mean": lambda n: round(n, 3) == 40.711,
            "stddev": lambda n: round(n, 4) == 0.0186,
            "plot": check_plot('histogram_numerical'),
        },
        {
            "name": "long",
            "structural_type": "http://schema.org/Float",
            "semantic_types": lambda l: "http://schema.org/longitude" in l,
            "mean": lambda n: round(n, 3) == -73.993,
            "stddev": lambda n: round(n, 5) == 0.00684,
            "plot": check_plot('histogram_numerical'),
        },
        {
            "name": "height",
            "structural_type": "http://schema.org/Float",
            "semantic_types": lambda l: isinstance(l, list),
            "mean": lambda n: round(n, 3) == 47.827,
            "stddev": lambda n: round(n, 2) == 21.28,
            "coverage": check_ranges(1.0, 90.0),
            "plot": check_plot('histogram_numerical'),
        }
    ],
    "spatial_coverage": [
        {
            "lat": "lat",
            "lon": "long",
            "ranges": check_geo_ranges(-74.006, 40.6905, -73.983, 40.7352)
        }
    ],
    "materialize": {
        "identifier": "datamart.test",
        "date": lambda d: isinstance(d, str)
    },
    "sample": "id,lat,long,height\nplace05,40.722948,-74.001501,42.904820\np" +
              "lace06,40.735108,-73.996996,48.345170\nplace14,40.733272,-73." +
              "996875,51.000673\nplace21,40.733305,-73.999205,45.887002\npla" +
              "ce25,40.727810,-73.999472,35.740136\nplace39,40.732095,-73.99" +
              "6864,47.361715\nplace41,40.727197,-73.996098,62.933509\nplace" +
              "44,40.730017,-73.993764,38.067007\nplace46,40.730439,-73.9966" +
              "33,32.522354\nplace47,40.736176,-73.998520,50.594276\nplace48" +
              ",40.730226,-74.001459,5.034845\nplace51,40.692165,-73.987300," +
              "67.055957\nplace55,40.693658,-73.984096,27.633986\nplace60,40" +
              ".691525,-73.987374,70.962950\nplace65,40.692605,-73.986475,53" +
              ".012337\nplace72,40.692980,-73.987301,46.909863\nplace74,40.6" +
              "93227,-73.988686,59.675767\nplace85,40.692914,-73.989237,73.3" +
              "57646\nplace87,40.693326,-73.984213,32.226852\nplace97,40.692" +
              "794,-73.986984,32.891257\n",
    "date": lambda d: isinstance(d, str),
    "version": version
}


geo_metadata_d3m = lambda v: {
    'about': {
        'datasetID': 'datamart.test.geo',
        'datasetName': 'geo',
        'description': 'Another simple CSV with places',
        'license': 'unknown',
        'approximateSize': '3910 B',
        'datasetSchemaVersion': v,
        'redacted': False,
        'datasetVersion': '1.0',
    },
    'dataResources': [
        {
            'resID': 'learningData',
            'resPath': 'tables/learningData.csv',
            'resType': 'table',
            'resFormat': ({'text/csv': ["csv"]} if v == '4.0.0'
                          else ['text/csv']),
            'isCollection': False,
            'columns': [
                {
                    'colIndex': 0,
                    'colName': 'id',
                    'colType': 'string',
                    'role': ['attribute'],
                },
                {
                    'colIndex': 1,
                    'colName': 'lat',
                    'colType': 'real',
                    'role': ['attribute'],
                },
                {
                    'colIndex': 2,
                    'colName': 'long',
                    'colType': 'real',
                    'role': ['attribute'],
                },
                {
                    'colIndex': 3,
                    'colName': 'height',
                    'colType': 'real',
                    'role': ['attribute'],
                },
            ],
        },
    ],
}


lazo_metadata = {
    'id': 'datamart.test.lazo',
    "name": "lazo",
    "description": "Simple CSV with states and years to test the Lazo index service",
    'source': 'fernando',
    "size": 297,
    "nb_rows": 36,
    "nb_profiled_rows": 36,
    "columns": [
        {
            "name": "state",
            "structural_type": "http://schema.org/Text",
            "semantic_types": [],
            "missing_values_ratio": lambda n: round(n, 4) == 0.0278,
            "num_distinct_values": 35,
        },
        {
            "name": "year",
            "structural_type": "http://schema.org/Integer",
            "semantic_types": [],
            "mean": lambda n: round(n, 2) == 1990.11,
            "stddev": lambda n: round(n, 4) == 0.3143,
            "coverage": (
                lambda l: sorted(l, key=lambda e: e['range']['gte']) == [
                    {
                        "range": {
                            "gte": 1990.0,
                            "lte": 1990.0
                        }
                    },
                    {
                        "range": {
                            "gte": 1991.0,
                            "lte": 1991.0
                        }
                    }
                ]
            ),
            "plot": check_plot('histogram_numerical'),
        }
    ],
    "materialize": {
        "direct_url": "http://test_discoverer:7000/lazo.csv",
        "identifier": "datamart.test",
        "date": lambda d: isinstance(d, str)
    },
    "sample": "state,year\nVA,1990\nKY,1990\nCA,1990\nWV,1990\nPR,1990\n" +
              "NC,1990\nAL,1990\nNJ,1990\nCT,1990\nCO,1990\n,1990\nMN,1990\n" +
              "OR,1990\nND,1990\nTN,1990\nGA,1990\nNM,1990\nAR,1990\n" +
              "UT,1991\nSC,1991\n",
    "date": lambda d: isinstance(d, str),
    "version": version
}


daily_metadata = {
    'id': 'datamart.test.daily',
    'name': 'daily',
    'description': 'Temporal dataset with daily resolution',
    'source': 'remi',
    'size': 448,
    'nb_rows': 30,
    "nb_profiled_rows": 30,
    'columns': [
        {
            'name': 'aug_date',
            'structural_type': 'http://schema.org/Text',
            'semantic_types': [
                'http://schema.org/DateTime',
            ],
            'num_distinct_values': 30,
            'mean': lambda n: round(n) == 1557230400.0,
            'stddev': lambda n: round(n, 2) == 747830.14,
            'coverage': (
                lambda l: sorted(l, key=lambda e: e['range']['gte']) == [
                    {
                        'range': {
                            'gte': 1555977600.0,
                            'lte': 1556755200.0,
                        },
                    },
                    {
                        'range': {
                            'gte': 1556841600.0,
                            'lte': 1557619200.0,
                        },
                    },
                    {
                        'range': {
                            'gte': 1557705600.0,
                            'lte': 1558483200.0,
                        },
                    },
                ]
            ),
            "plot": check_plot('histogram_temporal'),
        },
        {
            'name': 'rain',
            'structural_type': 'http://schema.org/Text',
            'semantic_types': [
                'http://schema.org/Boolean',
                'http://schema.org/Enumeration',
            ],
            'unclean_values_ratio': 0.0,
            'num_distinct_values': 2,
            "plot": check_plot('histogram_categorical'),
        },
    ],
    'materialize': {
        'identifier': 'datamart.test',
        'date': lambda d: isinstance(d, str),
    },
    'sample': "aug_date,rain\n2019-04-23,no\n2019-04-25,yes\n2019-04-26,no\n" +
              "2019-04-29,yes\n2019-05-02,no\n2019-05-03,yes\n2019-05-05,yes" +
              "\n2019-05-07,no\n2019-05-08,yes\n2019-05-09,yes\n2019-05-10,n" +
              "o\n2019-05-13,no\n2019-05-14,no\n2019-05-16,no\n2019-05-17,ye" +
              "s\n2019-05-18,no\n2019-05-19,yes\n2019-05-20,no\n2019-05-21,n" +
              "o\n2019-05-22,yes\n",
    'date': lambda d: isinstance(d, str),
    'version': version,
}


hourly_metadata = {
    'id': 'datamart.test.hourly',
    'name': 'hourly',
    'description': 'Temporal dataset with hourly resolution',
    'source': 'remi',
    'size': 1242,
    'nb_rows': 52,
    "nb_profiled_rows": 52,
    'columns': [
        {
            'name': 'aug_date',
            'structural_type': 'http://schema.org/Text',
            'semantic_types': [
                'http://schema.org/DateTime',
            ],
            'num_distinct_values': 52,
            'mean': lambda n: round(n) == 1560389398.0,
            'stddev': lambda n: round(n, 2) == 54027.44,
            'coverage': (
                lambda l: sorted(l, key=lambda e: e['range']['gte']) == [
                    {
                        'range': {
                            'gte': 1560297600.0,
                            'lte': 1560358784.0,
                        },
                    },
                    {
                        'range': {
                            'gte': 1560362368.0,
                            'lte': 1560419968.0,
                        },
                    },
                    {
                        'range': {
                            'gte': 1560423552.0,
                            'lte': 1560481152.0,
                        },
                    },
                ]
            ),
            "plot": check_plot('histogram_temporal'),
        },
        {
            'name': 'rain',
            'structural_type': 'http://schema.org/Text',
            'semantic_types': [
                'http://schema.org/Boolean',
                'http://schema.org/Enumeration',
            ],
            'unclean_values_ratio': 0.0,
            'num_distinct_values': 2,
            "plot": check_plot('histogram_categorical'),
        },
    ],
    'materialize': {
        'direct_url': 'http://test_discoverer:7000/hourly.csv',
        'identifier': 'datamart.test',
        'date': lambda d: isinstance(d, str),
    },
    'sample': "aug_date,rain\n2019-06-12T01:00:00,no\n2019-06-12T02:00:00,no" +
              "\n2019-06-12T03:00:00,yes\n2019-06-12T09:00:00,no\n2019-06-12" +
              "T10:00:00,yes\n2019-06-12T11:00:00,yes\n2019-06-12T12:00:00,y" +
              "es\n2019-06-12T14:00:00,yes\n2019-06-12T15:00:00,no\n2019-06-" +
              "12T20:00:00,yes\n2019-06-12T21:00:00,yes\n2019-06-13T01:00:00" +
              ",no\n2019-06-13T03:00:00,no\n2019-06-13T05:00:00,no\n2019-06-" +
              "13T07:00:00,yes\n2019-06-13T10:00:00,yes\n2019-06-13T14:00:00" +
              ",yes\n2019-06-13T17:00:00,yes\n2019-06-14T00:00:00,yes\n2019-" +
              "06-14T01:00:00,yes\n",
    'date': lambda d: isinstance(d, str),
    'version': version,
}
