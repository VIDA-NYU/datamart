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
from urllib.parse import urlparse, urlunparse, parse_qs
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
    'required': ['results'],
    'additionalProperties': False,
    'definitions': result_schema.pop('definitions'),
}
metadata_schema = dict(result_schema)
assert metadata_schema['required'] == ['id', 'score', 'metadata']
metadata_schema['required'] = ['id', 'status', 'metadata']
metadata_schema['properties'] = dict(
    metadata_schema['properties'],
    status={'type': 'string'},
)


class DatamartTest(DataTestCase):
    def datamart_get(self, url, **kwargs):
        return self._request('get', url, **kwargs)

    def datamart_post(self, url, **kwargs):
        return self._request('post', url, **kwargs)

    def _request(self, method, url, schema=None, check_status=True, **kwargs):
        if 'files' in kwargs:
            # Read files now
            # If we retry, requests would read un-rewinded files
            files = {}
            for k, v in kwargs['files'].items():
                if isinstance(v, (list, tuple)):
                    v = (
                        v[0],
                        v[1].read() if hasattr(v[1], 'read') else v[1],
                    ) + v[2:]
                elif hasattr(v, 'read'):
                    v = v.read()
                files[k] = v
            kwargs['files'] = files

        response = requests.request(
            method,
            os.environ['API_URL'] + url,
            **kwargs
        )
        for _ in range(5):
            if response.status_code != 503:
                break
            time.sleep(0.5)
            response = requests.request(
                method,
                os.environ['API_URL'] + url,
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
            size=100,
        )['hits']['hits']
        hits = {h['_id']: h['_source'] for h in hits}

        self.assertJson(
            hits,
            {
                'datamart.test.basic': basic_metadata,
                'datamart.test.geo': geo_metadata,
                'datamart.test.geo_wkt': geo_wkt_metadata,
                'datamart.test.agg': agg_metadata,
                'datamart.test.lazo': lazo_metadata,
                'datamart.test.daily': daily_metadata,
                'datamart.test.hourly': hourly_metadata,
                'datamart.test.dates_pivoted': dates_pivoted_metadata,
                'datamart.test.excel': other_formats_metadata('xls'),
                'datamart.test.spss': other_formats_metadata('spss'),
            },
        )

    def test_alternate(self):
        es = elasticsearch.Elasticsearch(
            os.environ['ELASTICSEARCH_HOSTS'].split(',')
        )
        hits = es.search(
            index='pending',
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
                'datamart.test.empty': {
                    'status': 'error',
                    'error': "Dataset has no rows",
                    'source': 'remi',
                    'date': lambda d: isinstance(d, str),
                    'metadata': {
                        'description': "A CSV with no rows to test " +
                                       "alternate index",
                        'source': 'remi',
                        'name': 'empty',
                        'size': 28,
                        'nb_rows': 0,
                        'nb_profiled_rows': 0,
                        'columns': [
                            {'name': 'important features'},
                            {'name': 'not here'},
                        ],
                        'materialize': {
                            'identifier': 'datamart.test',
                            'direct_url': 'http://test-discoverer:7000' +
                                          '/empty.csv',
                            'date': lambda d: isinstance(d, str),
                        },
                    },
                    'materialize': {
                        'identifier': 'datamart.test',
                        'direct_url': 'http://test-discoverer:7000/empty.csv',
                        'date': lambda d: isinstance(d, str),
                    },
                },
            },
        )

    def test_indexes(self):
        def hide_default_analyzers(value):
            if isinstance(value, dict):
                if value.get('analyzer') == 'default':
                    del value['analyzer']
                if value.get('search_analyzer') == 'default_search':
                    del value['search_analyzer']
                for v in value.values():
                    hide_default_analyzers(v)
            elif isinstance(value, list):
                for v in value:
                    hide_default_analyzers(v)

        response = requests.get(
            'http://' + os.environ['ELASTICSEARCH_HOSTS'].split(',')[0] +
            '/_all'
        )
        response.raise_for_status()
        actual = response.json()
        with pkg_resources.resource_stream(
                'coordinator', 'elasticsearch.yml') as stream:
            expected = yaml.safe_load(stream)
        expected.pop('_refs', None)

        # Remove 'lazo' index
        actual.pop('lazo', None)

        # Remove variable sections
        for index in expected.values():
            index.setdefault('aliases', {})
            hide_default_analyzers(index)
        for index in actual.values():
            hide_default_analyzers(index)
            settings = index['settings']['index']
            settings.pop('creation_date', None)
            settings.pop('number_of_replicas', None)
            settings.pop('number_of_shards', None)
            settings.pop('provided_name', None)
            settings.pop('uuid', None)
            settings.pop('version', None)

        # Add custom fields
        for idx, prefix in [
            ('datamart', ''),
            ('datamart_columns', 'dataset_'),
            ('datamart_spatial_coverage', 'dataset_'),
        ]:
            props = expected[idx]['mappings']['properties']
            props[prefix + 'specialId'] = {'type': 'integer'}
            props[prefix + 'dept'] = {'type': 'keyword'}

        self.assertJson(actual, expected)


class TestProfileQuery(DatamartTest):
    def check_result(self, response, metadata, token):
        # Some fields like 'name', 'description' won't be there
        metadata = {k: v for k, v in metadata.items()
                    if k not in {'id', 'name', 'description',
                                 'source', 'date'}}
        metadata['materialize'] = {k: v
                                   for k, v in metadata['materialize'].items()
                                   if k == 'convert'}
        # Plots are not computed, remove them too
        metadata['columns'] = [
            {k: v for k, v in col.items() if k != 'plot'}
            for col in metadata['columns']
        ]
        # Handle lazo data
        check_lazo = lambda dct: (
            dct.keys() == {'cardinality', 'hash_values', 'n_permutations'}
        )
        for column in metadata['columns']:
            if column['structural_type'] == 'http://schema.org/Text':
                column['lazo'] = check_lazo
        # Expect token
        metadata['token'] = token

        self.assertJson(response.json(), metadata)

    def test_basic(self):
        with data('basic.csv') as basic_fp:
            response = self.datamart_post(
                '/profile',
                files={'data': basic_fp},
            )
        self.check_result(
            response,
            basic_metadata,
            'cac18c69aff995773bed73273421365006e5e0b6',
        )

    def test_excel(self):
        with data('excel.xlsx') as excel_fp:
            response = self.datamart_post(
                '/profile',
                files={'data': excel_fp},
            )
        self.check_result(
            response,
            other_formats_metadata('xls'),
            '87ef93cd71b93b0a1a6956a0281dbb8db69feb48',
        )


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
            json={'source': 'fernando'},
            schema=result_list_schema,
        )
        results = response.json()['results']
        self.assertEqual(
            {r['id'] for r in results},
            {'datamart.test.agg', 'datamart.test.lazo'},
        )

    def test_search_temporal_resolution(self):
        """Search restricted on temporal resolution."""
        response = self.datamart_post(
            '/search',
            json={
                'keywords': 'daily',
                'variables': [{
                    'type': 'temporal_variable',
                    'granularity': 'hour',
                }],
            },
            schema=result_list_schema,
        )
        results = response.json()['results']
        self.assertEqual({r['id'] for r in results}, set())

        response = self.datamart_post(
            '/search',
            json={
                'keywords': 'daily',
                'variables': [{
                    'type': 'temporal_variable',
                    'granularity': 'day',
                }],
            },
            schema=result_list_schema,
        )
        results = response.json()['results']
        self.assertEqual({r['id'] for r in results}, {'datamart.test.daily'})


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
                    'd3m_dataset_description': lambda d: isinstance(d, dict),
                    'score': lambda n: isinstance(n, float) and n > 0.0,
                    'augmentation': {
                        'left_columns': [[0]],
                        'left_columns_names': [['orig_date']],
                        'right_columns': [[0]],
                        'right_columns_names':[['aug_date']],
                        'type':'join',
                        'temporal_resolution': 'day',
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
                    'd3m_dataset_description': lambda d: isinstance(d, dict),
                    'score': lambda n: isinstance(n, float) and n > 0.0,
                    'augmentation': {
                        'left_columns': [[0]],
                        'left_columns_names': [['orig_date']],
                        'right_columns': [[0]],
                        'right_columns_names':[['aug_date']],
                        'type':'join',
                        'temporal_resolution': 'hour',
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
                        'temporal_resolution': 'day',
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
                         'http://test-discoverer:7000/basic.csv')

        response = self.datamart_get('/download/' + 'datamart.test.basic',
                                     # explicit format
                                     params={'format': 'csv'},
                                     allow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'],
                         'http://test-discoverer:7000/basic.csv')

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
            '/metadata/' + 'datamart.test.basic',
            schema=metadata_schema,
        )
        basic_meta = basic_meta.json()['metadata']

        response = self.datamart_post(
            '/download', allow_redirects=False,
            params={'format': 'd3m', 'format_version': '3.2.0'},
            files={
                'task': json.dumps({
                    'id': 'datamart.test.basic',
                    'score': 1.0,
                    'metadata': basic_meta
                }).encode('utf-8'),
            },
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
            files={
                'task': json.dumps({
                    'id': 'datamart.test.basic',
                }).encode('utf-8'),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'],
                         'http://test-discoverer:7000/basic.csv')

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
                         'http://test-discoverer:7000/basic.csv')

        # Geo dataset, materialized via /datasets storage
        geo_meta = self.datamart_get(
            '/metadata/' + 'datamart.test.geo',
            schema=metadata_schema,
        )
        geo_meta = geo_meta.json()['metadata']

        response = self.datamart_post(
            '/download', allow_redirects=False,
            # format defaults to csv
            files={
                'task': json.dumps({
                    'id': 'datamart.test.geo',
                    'score': 1.0,
                    'metadata': geo_meta
                }).encode('utf-8'),
            },
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
            files={
                'task': json.dumps({
                    'id': 'datamart.nonexistent',
                    'score': 0.0,
                    'metadata': {
                        'name': "Non-existent dataset",
                        'materialize': {
                            'identifier': 'datamart.nonexistent',
                        }
                    }
                }).encode('utf-8'),
            },
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
                os.environ['API_URL'],
                'pandas',
            )
            self.assertEqual(df.shape, (8, 3))

            datamart_materialize.download(
                'datamart.test.geo',
                os.path.join(tempdir, 'geo.csv'),
                os.environ['API_URL'],
            )
            assert_same_files(
                os.path.join(tempdir, 'geo.csv'),
                os.path.join(os.path.dirname(__file__), 'data/geo.csv'),
            )

            datamart_materialize.download(
                'datamart.test.agg',
                os.path.join(tempdir, 'agg'),
                os.environ['API_URL'],
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
            params={'format': 'd3m', 'format_need_d3mindex': '1'},
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
    def check_basic_join(self, response):
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
                        'approximateSize': '167 B',
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

    def test_basic_join(self):
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.basic',
            schema=metadata_schema,
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
        self.check_basic_join(response)

    def test_basic_join_data_token(self):
        # Build task dictionary
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.basic',
            schema=metadata_schema,
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

        # Get data token
        with data('basic_aug.csv') as basic_aug:
            response = self.datamart_post(
                '/profile',
                files={
                    'data': basic_aug,
                },
            )
        self.assertTrue(
            response.headers['Content-Type'].startswith('application/json')
        )
        token = response.json()['token']
        self.assertEqual(len(token), 40)

        response = self.datamart_post(
            '/augment',
            files={
                'task': json.dumps(task).encode('utf-8'),
                'data': token.encode('ascii'),
            }
        )
        self.check_basic_join(response)

    def test_basic_join_auto(self):
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.basic',
            schema=metadata_schema,
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
        self.check_basic_join(response)

    def test_agg_join(self):
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.agg',
            schema=metadata_schema,
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
                '/augment?format=csv',
                files={
                    'task': json.dumps(task).encode('utf-8'),
                    'data': agg_aug,
                },
            )
        self.assertEqual(
            response.headers['Content-Type'],
            'application/octet-stream',
        )
        self.assertTrue(
            response.headers['Content-Disposition'].startswith('attachment')
        )
        self.assertCsvEqualNoOrder(
            response.content.decode('utf-8'),
            'id,location,work,mean salary,sum salary,max salary,min salary',
            [
                '30,south korea,True,150.0,300.0,200.0,100.0',
                '40,brazil,False,,,,',
                '70,usa,True,600.0,600.0,600.0,600.0',
                '80,canada,True,200.0,200.0,200.0,200.0',
                '100,france,False,250.0,500.0,300.0,200.0',
            ],
        )

    def test_agg_join_specific_functions(self):
        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.agg',
            schema=metadata_schema,
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
                    '30,south korea,2,200.0,300.0,200.0',
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
                        'approximateSize': '197 B',
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
                                    'colType': 'categorical',
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
            '/metadata/' + 'datamart.test.lazo',
            schema=metadata_schema,
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
                    'Pa,1990.0,1990.0,1990.0,1990.0',
                    'sd,,,,',
                    'nj,1990.0,1990.0,1990.0,1990.0',
                    'NH,,,,',
                    'TX,1990.0,1990.0,1990.0,1990.0',
                    'mS,1990.0,1990.0,1990.0,1990.0',
                    'Tn,1990.0,1990.0,1990.0,1990.0',
                    'WA,1990.0,1990.0,1990.0,1990.0',
                    'va,1990.0,1990.0,1990.0,1990.0',
                    'NY,1990.0,1990.0,1990.0,1990.0',
                    'oh,1990.0,1990.0,1990.0,1990.0',
                    'or,1990.0,1990.0,1990.0,1990.0',
                    'IL,1990.0,1990.0,1990.0,1990.0',
                    'MT,,,,',
                    'hi,,,,',
                    'Ca,1990.0,1990.0,1990.0,1990.0',
                    'nC,1990.0,1990.0,1990.0,1990.0',
                    'Ut,1991.0,1991.0,1991.0,1991.0',
                    'sC,1991.0,1991.0,1991.0,1991.0',
                    'La,1990.0,1990.0,1990.0,1990.0',
                    'ME,1990.0,1990.0,1990.0,1990.0',
                    'MI,1990.0,1990.0,1990.0,1990.0',
                    'nE,1990.0,1990.0,1990.0,1990.0',
                    'In,1990.0,1990.0,1990.0,1990.0',
                    'ND,1990.0,1990.0,1990.0,1990.0',
                ],
            )
        with zip_.open('datasetDoc.json') as meta_fp:
            meta = json.load(meta_fp)
            self.assertJson(
                meta,
                {
                    'about': {
                        'approximateSize': '787 B',
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
            '/metadata/' + 'datamart.test.geo',
            schema=metadata_schema,
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
                ','.join(e[:8] for e in line.split(','))
                for line in table_lines
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
                        'approximateSize': '3798 B',
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
            '/metadata/' + 'datamart.test.daily',
            schema=metadata_schema,
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
            '/metadata/' + 'datamart.test.hourly',
            schema=metadata_schema,
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
            '/metadata/' + 'datamart.test.hourly',
            schema=metadata_schema,
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
            '/metadata/' + 'datamart.test.daily',
            schema=metadata_schema,
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


class TestUpload(DatamartTest):
    def test_upload(self):
        response = self.datamart_post(
            '/upload',
            data={
                'address': 'http://test-discoverer:7000/basic.csv',
                'name': 'basic reupload',
                'description': "sent through upload endpoint",
                'specialId': 12,
                'dept': "internal",
            },
            schema={
                'type': 'object',
                'properties': {
                    'id': {'type': 'string'},
                },
                'required': ['id'],
                'additionalProperties': False,
            },
        )
        record = response.json()
        self.assertEqual(record.keys(), {'id'})
        dataset_id = record['id']
        self.assertTrue(dataset_id.startswith('datamart.url.'))

        es = elasticsearch.Elasticsearch(
            os.environ['ELASTICSEARCH_HOSTS'].split(',')
        )

        try:
            # Check it's in the alternate index
            try:
                pending = es.get('pending', dataset_id)['_source']
                self.assertJson(
                    pending,
                    {
                        'status': 'queued',
                        'date': lambda d: isinstance(d, str),
                        'source': 'upload',
                        'metadata': {
                            'name': 'basic reupload',
                            'description': 'sent through upload endpoint',
                            'specialId': 12,
                            'dept': "internal",
                            'source': 'upload',
                            'materialize': {
                                'identifier': 'datamart.url',
                                'direct_url': 'http://test-discoverer:7000/basic.csv',
                                'date': lambda d: isinstance(d, str),
                            },
                        },
                        'materialize': {
                            'identifier': 'datamart.url',
                            'direct_url': 'http://test-discoverer:7000/basic.csv',
                            'date': lambda d: isinstance(d, str),
                        },
                    },
                )
            finally:
                # Wait for it to be indexed
                for _ in range(10):
                    try:
                        record = es.get('datamart', dataset_id)['_source']
                    except elasticsearch.NotFoundError:
                        pass
                    else:
                        break
                    time.sleep(2)
                else:
                    self.fail("Dataset didn't make it to index")

            self.assertJson(
                record,
                dict(
                    basic_metadata,
                    id=dataset_id,
                    name='basic reupload',
                    description="sent through upload endpoint",
                    specialId=12,
                    dept="internal",
                    source='upload',
                    materialize=dict(
                        basic_metadata['materialize'],
                        identifier='datamart.url',
                    ),
                ),
            )

            # Check it's no longer in alternate index
            time.sleep(1)
            with self.assertRaises(elasticsearch.NotFoundError):
                es.get('pending', dataset_id)
        finally:
            import lazo_index_service
            from datamart_core.common import delete_dataset_from_index

            time.sleep(3)  # Deleting won't work immediately
            lazo_client = lazo_index_service.LazoIndexClient(
                host=os.environ['LAZO_SERVER_HOST'],
                port=int(os.environ['LAZO_SERVER_PORT'])
            )
            delete_dataset_from_index(
                es,
                dataset_id,
                lazo_client,
            )


class TestSession(DatamartTest):
    def test_session_new(self):
        def new_session(obj):
            response = self.datamart_post(
                '/session/new',
                json=obj,
            )
            obj = response.json()
            session_id = obj.pop('session_id')
            link_url = obj.pop('link_url')
            self.assertFalse(obj.keys())
            link_url = urlparse(link_url)
            self.assertEqual(
                urlunparse(link_url[:4] + (('',) * 2)),
                os.environ['FRONTEND_URL'] + '/',
            )
            query = parse_qs(link_url.query)
            session, = query['session']
            self.assertFalse(obj.keys())
            return session_id, json.loads(session)

        session_id, session_obj = new_session({})
        self.assertEqual(
            session_obj,
            {
                'format': 'csv',
                'format_options': {},
                'session_id': session_id,
                'system_name': 'TA3',
            },
        )

        session_id, session_obj = new_session({'data_token': 'a94a8fe5ccb19ba61c4c0873d391e987982fbbd3'})
        self.assertEqual(
            session_obj,
            {
                'format': 'csv',
                'format_options': {},
                'session_id': session_id,
                'data_token': 'a94a8fe5ccb19ba61c4c0873d391e987982fbbd3',
                'system_name': 'TA3',
            },
        )

        session_id, session_obj = new_session({
            'system_name': 'Modeler',
            'format': 'd3m',
        })
        self.assertEqual(
            session_obj,
            {
                'format': 'd3m',
                'format_options': {'need_d3mindex': False, 'version': '4.0.0'},
                'session_id': session_id,
                'system_name': 'Modeler',
            },
        )

        response = self.datamart_post(
            '/session/new',
            json={'unknown_key': 'value'},
            check_status=False,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {'error': "Unrecognized key 'unknown_key'"},
        )

    def test_download_csv(self):
        session_id = self.datamart_post(
            '/session/new',
            json={'format': 'csv'},
        ).json()['session_id']

        response = self.datamart_get(
            '/download/' + 'datamart.test.basic'
            + '?session_id=' + session_id
        )
        self.assertEqual(response.json(), {'success': "attached to session"})

        response = self.datamart_get(
            '/download/' + 'datamart.test.agg'
            + '?session_id=' + session_id
        )
        self.assertEqual(response.json(), {'success': "attached to session"})

        response = self.datamart_get('/session/' + session_id)
        self.assertEqual(
            response.json(),
            {
                'results': [
                    {
                        'url': (os.environ['API_URL']
                                + '/download/datamart.test.basic'
                                + '?format=csv'),
                    },
                    {
                        'url': (os.environ['API_URL']
                                + '/download/datamart.test.agg'
                                + '?format=csv'),
                    },
                ],
            },
        )

    def test_download_d3m(self):
        session_id = self.datamart_post(
            '/session/new',
            json={'format': 'd3m'},
        ).json()['session_id']

        response = self.datamart_get(
            '/download/' + 'datamart.test.basic'
            + f'?session_id={session_id}&format=d3m'
        )
        self.assertEqual(response.json(), {'success': "attached to session"})

        response = self.datamart_get(
            '/download/' + 'datamart.test.agg'
            + f'?session_id={session_id}&format=d3m'
        )
        self.assertEqual(response.json(), {'success': "attached to session"})

        response = self.datamart_get('/session/' + session_id)
        format_query = (
            'format=d3m'
            + '&format_version=4.0.0'
            + '&format_need_d3mindex=False'
        )
        self.assertEqual(
            response.json(),
            {
                'results': [
                    {
                        'url': (
                            os.environ['API_URL']
                            + '/download/datamart.test.basic'
                            + '?' + format_query
                        ),
                    },
                    {
                        'url': (os.environ['API_URL']
                                + '/download/datamart.test.agg'
                                + '?' + format_query),
                    },
                ],
            },
        )

    def test_augment_csv(self):
        session_id = self.datamart_post(
            '/session/new',
            json={'format': 'csv'},
        ).json()['session_id']

        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.basic',
            schema=metadata_schema,
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
                '/augment'
                + f'?session_id={session_id}&format=csv',
                files={
                    'task': json.dumps(task).encode('utf-8'),
                    'data': basic_aug,
                },
            )
        self.assertEqual(response.json(), {'success': "attached to session"})

        response = self.datamart_get('/session/' + session_id)
        self.assertJson(
            response.json(),
            {
                'results': [
                    {
                        'url': lambda u: u.startswith(
                            os.environ['API_URL'] + '/augment/'
                        ),
                    },
                ],
            },
        )
        result_id = response.json()['results'][0]['url'][-40:]

        response = self.datamart_get(
            '/augment/' + result_id,
        )
        self.assertEqual(
            response.headers['Content-Type'],
            'application/octet-stream',
        )

    def test_augment_d3m(self):
        session_id = self.datamart_post(
            '/session/new',
            json={'format': 'd3m'},
        ).json()['session_id']

        meta = self.datamart_get(
            '/metadata/' + 'datamart.test.basic',
            schema=metadata_schema,
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
                '/augment'
                + f'?session_id={session_id}&format=d3m',
                files={
                    'task': json.dumps(task).encode('utf-8'),
                    'data': basic_aug,
                },
            )
        self.assertEqual(response.json(), {'success': "attached to session"})

        response = self.datamart_get('/session/' + session_id)
        self.assertJson(
            response.json(),
            {
                'results': [
                    {
                        'url': lambda u: u.startswith(
                            os.environ['API_URL'] + '/augment/'
                        ),
                    },
                ],
            },
        )
        result_id = response.json()['results'][0]['url'][-40:]

        response = self.datamart_get(
            '/augment/' + result_id,
        )
        self.assertEqual(
            response.headers['Content-Type'],
            'application/zip',
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
            "unclean_values_ratio": 0.0,
            'num_distinct_values': 5,
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
        "direct_url": "http://test-discoverer:7000/basic.csv",
        "identifier": "datamart.test",
        "date": lambda d: isinstance(d, str)
    },
    "sample": "name,country,number,what\r\njames,canada,5,false\r\n" +
              "john,usa,4,false\r\nrobert,usa,6,false\r\nmichael,usa,7,true" +
              "\r\nwilliam,usa,7,true\r\ndavid,canada,5,false\r\n" +
              "richard,canada,7,true\r\njoseph,usa,6,true\r\n" +
              "thomas,usa,6,false\r\ncharles,usa,7,false\r\n" +
              "christopher,canada,11,true\r\ndaniel,usa,5,false\r\n"
              "matthew,canada,7,true\r\nanthony,canada,7,true\r\n" +
              "donald,usa,6,true\r\nmark,usa,4,false\r\npaul,usa,4,false\r\n" +
              "steven,usa,6,false\r\nandrew,canada,6,false\r\n" +
              "kenneth,canada,7,true\r\n",
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
            "unclean_values_ratio": 0.0,
            'num_distinct_values': 5,
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
            "unclean_values_ratio": 0.0,
            'num_distinct_values': 4,
            "mean": lambda n: round(n, 2) == 266.67,
            "stddev": lambda n: round(n, 3) == 159.861,
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
    "sample": "id,work,salary\r\n40,false,\r\n30,true,200\r\n70,true,\r\n80," +
              "true,200\r\n100,false,300\r\n100,true,200\r\n30,false,100\r\n" +
              "70,false,600\r\n",
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
            "unclean_values_ratio": 0.0,
            "mean": lambda n: round(n, 3) == 40.711,
            "stddev": lambda n: round(n, 4) == 0.0186,
            "plot": check_plot('histogram_numerical'),
        },
        {
            "name": "long",
            "structural_type": "http://schema.org/Float",
            "semantic_types": lambda l: "http://schema.org/longitude" in l,
            "unclean_values_ratio": 0.0,
            "mean": lambda n: round(n, 3) == -73.993,
            "stddev": lambda n: round(n, 5) == 0.00684,
            "plot": check_plot('histogram_numerical'),
        },
        {
            "name": "height",
            "structural_type": "http://schema.org/Float",
            "semantic_types": lambda l: isinstance(l, list),
            "unclean_values_ratio": 0.0,
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
    "sample": "id,lat,long,height\r\nplace05,40.722948,-74.001501,42.904820" +
              "\r\nplace06,40.735108,-73.996996,48.345170\r\nplace14,40.7332" +
              "72,-73.996875,51.000673\r\nplace21,40.733305,-73.999205,45.88" +
              "7002\r\nplace25,40.727810,-73.999472,35.740136\r\nplace39,40." +
              "732095,-73.996864,47.361715\r\nplace41,40.727197,-73.996098,6" +
              "2.933509\r\nplace44,40.730017,-73.993764,38.067007\r\nplace46" +
              ",40.730439,-73.996633,32.522354\r\nplace47,40.736176,-73.9985" +
              "20,50.594276\r\nplace48,40.730226,-74.001459,5.034845\r\nplac" +
              "e51,40.692165,-73.987300,67.055957\r\nplace55,40.693658,-73.9" +
              "84096,27.633986\r\nplace60,40.691525,-73.987374,70.962950\r\n" +
              "place65,40.692605,-73.986475,53.012337\r\nplace72,40.692980,-" +
              "73.987301,46.909863\r\nplace74,40.693227,-73.988686,59.675767" +
              "\r\nplace85,40.692914,-73.989237,73.357646\r\nplace87,40.6933" +
              "26,-73.984213,32.226852\r\nplace97,40.692794,-73.986984,32.89" +
              "1257\r\n",
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


geo_wkt_metadata = {
    "id": "datamart.test.geo_wkt",
    "name": "geo_wkt",
    "description": "Simple CSV in WKT format",
    'source': 'remi',
    "size": 4708,
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
            "name": "coords",
            "structural_type": "http://schema.org/GeoCoordinates",
            "semantic_types": [],
            "unclean_values_ratio": 0.0,
        },
        {
            "name": "height",
            "structural_type": "http://schema.org/Float",
            "semantic_types": lambda l: isinstance(l, list),
            "unclean_values_ratio": 0.0,
            "mean": lambda n: round(n, 3) == 47.827,
            "stddev": lambda n: round(n, 2) == 21.28,
            "coverage": check_ranges(1.0, 90.0),
            "plot": check_plot('histogram_numerical'),
        }
    ],
    "spatial_coverage": [
        {
            "point": "coords",
            "ranges": check_geo_ranges(-74.006, 40.6905, -73.983, 40.7352)
        }
    ],
    "materialize": {
        "identifier": "datamart.test",
        "date": lambda d: isinstance(d, str),
        "direct_url": "http://test-discoverer:7000/geo_wkt.csv",
    },
    "sample": "id,coords,height\r\nplace05,POINT (-74.001501 40.722948),42.9" +
              "04820\r\nplace06,POINT (-73.996996 40.735108),48.345170\r\npl" +
              "ace14,POINT (-73.996875 40.733272),51.000673\r\nplace21,POINT" +
              " (-73.999205 40.733305),45.887002\r\nplace25,POINT (-73.99947" +
              "2 40.727810),35.740136\r\nplace39,POINT (-73.996864 40.732095" +
              "),47.361715\r\nplace41,POINT (-73.996098 40.727197),62.933509" +
              "\r\nplace44,POINT (-73.993764 40.730017),38.067007\r\nplace46" +
              ",POINT (-73.996633 40.730439),32.522354\r\nplace47,POINT (-73" +
              ".998520 40.736176),50.594276\r\nplace48,POINT (-74.001459 40." +
              "730226),5.034845\r\nplace51,POINT (-73.987300 40.692165),67.0" +
              "55957\r\nplace55,POINT (-73.984096 40.693658),27.633986\r\npl" +
              "ace60,POINT (-73.987374 40.691525),70.962950\r\nplace65,POINT" +
              " (-73.986475 40.692605),53.012337\r\nplace72,POINT (-73.98730" +
              "1 40.692980),46.909863\r\nplace74,POINT (-73.988686 40.693227" +
              "),59.675767\r\nplace85,POINT (-73.989237 40.692914),73.357646" +
              "\r\nplace87,POINT (-73.984213 40.693326),32.226852\r\nplace97" +
              ",POINT (-73.986984 40.692794),32.891257\r\n",
    "date": lambda d: isinstance(d, str),
    "version": version
}


lazo_metadata = {
    'id': 'datamart.test.lazo',
    "name": "lazo",
    "description": "Simple CSV with states and years to test the Lazo index service",
    'source': 'fernando',
    "size": 334,
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
            "semantic_types": ["http://schema.org/DateTime"],
            "unclean_values_ratio": 0.0,
            'num_distinct_values': 2,
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
            "temporal_resolution": "year",
            "plot": check_plot('histogram_numerical'),
        }
    ],
    "materialize": {
        "direct_url": "http://test-discoverer:7000/lazo.csv",
        "identifier": "datamart.test",
        "date": lambda d: isinstance(d, str),
        "convert": [{'identifier': 'tsv'}],
    },
    "sample": "state,year\r\nVA,1990\r\nKY,1990\r\nCA,1990\r\nWV,1990\r\nPR," +
              "1990\r\nNC,1990\r\nAL,1990\r\nNJ,1990\r\nCT,1990\r\nCO,1990\r" +
              "\n,1990\r\nMN,1990\r\nOR,1990\r\nND,1990\r\nTN,1990\r\nGA,199" +
              "0\r\nNM,1990\r\nAR,1990\r\nUT,1991\r\nSC,1991\r\n",
    "date": lambda d: isinstance(d, str),
    "version": version
}


daily_metadata = {
    'id': 'datamart.test.daily',
    'name': 'daily',
    'description': 'Temporal dataset with daily resolution',
    'source': 'remi',
    'size': 388,
    'nb_rows': 30,
    "nb_profiled_rows": 30,
    'columns': [
        {
            'name': 'aug_date',
            'structural_type': 'http://schema.org/Text',
            'semantic_types': [
                'http://schema.org/DateTime',
            ],
            'unclean_values_ratio': 0.0,
            'temporal_resolution': 'day',
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
    'sample': "aug_date,rain\r\n20190423,no\r\n20190425,yes\r\n20190426,no\r" +
              "\n20190429,yes\r\n20190502,no\r\n20190503,yes\r\n20190505,yes" +
              "\r\n20190507,no\r\n20190508,yes\r\n20190509,yes\r\n20190510,n" +
              "o\r\n20190513,no\r\n20190514,no\r\n20190516,no\r\n20190517,ye" +
              "s\r\n20190518,no\r\n20190519,yes\r\n20190520,no\r\n20190521,n" +
              "o\r\n20190522,yes\r\n",
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
            'temporal_resolution': 'hour',
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
        'direct_url': 'http://test-discoverer:7000/hourly.csv',
        'identifier': 'datamart.test',
        'date': lambda d: isinstance(d, str),
    },
    'sample': "aug_date,rain\r\n2019-06-12T01:00:00,no\r\n2019-06-12T02:00:0" +
              "0,no\r\n2019-06-12T03:00:00,yes\r\n2019-06-12T09:00:00,no\r\n" +
              "2019-06-12T10:00:00,yes\r\n2019-06-12T11:00:00,yes\r\n2019-06" +
              "-12T12:00:00,yes\r\n2019-06-12T14:00:00,yes\r\n2019-06-12T15:" +
              "00:00,no\r\n2019-06-12T20:00:00,yes\r\n2019-06-12T21:00:00,ye" +
              "s\r\n2019-06-13T01:00:00,no\r\n2019-06-13T03:00:00,no\r\n2019" +
              "-06-13T05:00:00,no\r\n2019-06-13T07:00:00,yes\r\n2019-06-13T1" +
              "0:00:00,yes\r\n2019-06-13T14:00:00,yes\r\n2019-06-13T17:00:00" +
              ",yes\r\n2019-06-14T00:00:00,yes\r\n2019-06-14T01:00:00,yes\r\n",
    'date': lambda d: isinstance(d, str),
    'version': version,
}


dates_pivoted_metadata = {
    'id': 'datamart.test.dates_pivoted',
    'name': 'dates pivoted',
    'description': 'Temporal dataset but in columns',
    'source': 'remi',
    'size': 525,
    'nb_rows': 24,
    'nb_profiled_rows': 24,
    'columns': [
        {
            'name': 'country',
            'structural_type': 'http://schema.org/Text',
            'semantic_types': [
                'http://schema.org/Enumeration',
            ],
            'num_distinct_values': 2,
            'plot': check_plot('histogram_categorical'),
        },
        {
            'name': 'date',
            'structural_type': 'http://schema.org/Text',
            'semantic_types': [
                'http://schema.org/DateTime',
            ],
            'num_distinct_values': 12,
            'mean': 1339833600.0,
            'stddev': 9093802.373045063,
            'coverage': check_ranges(1325376000.0, 1354320000.0),
            'temporal_resolution': 'month',
            'plot': check_plot('histogram_temporal'),
        },
        {
            'name': 'value',
            'structural_type': 'http://schema.org/Text',
            'unclean_values_ratio': 0.0,
            'semantic_types': [
                'http://schema.org/Boolean',
                'http://schema.org/Enumeration',
            ],
            'num_distinct_values': 2,
            'plot': check_plot('histogram_categorical'),
        },
    ],
    'materialize': {
        'direct_url': 'http://test-discoverer:7000/dates_pivoted.csv',
        'identifier': 'datamart.test',
        'date': lambda d: isinstance(d, str),
        'convert': [
            {'identifier': 'pivot', 'except_columns': [0]},
        ],
    },
    'sample': "country,date,value\r\nfrance,2012-01-01,yes\r\nfrance,2012-02" +
              "-01,no\r\nfrance,2012-03-01,no\r\nfrance,2012-04-01,yes\r\nfr" +
              "ance,2012-06-01,yes\r\nfrance,2012-07-01,yes\r\nfrance,2012-0" +
              "8-01,yes\r\nfrance,2012-09-01,yes\r\nfrance,2012-10-01,no\r\n" +
              "france,2012-11-01,no\r\nusa,2012-01-01,no\r\nusa,2012-03-01,y" +
              "es\r\nusa,2012-04-01,yes\r\nusa,2012-05-01,no\r\nusa,2012-06-" +
              "01,no\r\nusa,2012-07-01,no\r\nusa,2012-09-01,no\r\nusa,2012-1" +
              "0-01,yes\r\nusa,2012-11-01,yes\r\nusa,2012-12-01,no\r\n",
    'date': lambda d: isinstance(d, str),
    'version': version
}


other_formats_metadata = lambda fmt: {
    'id': lambda v: isinstance(v, str),
    'name': lambda v: isinstance(v, str),
    'description': lambda v: isinstance(v, str),
    'source': 'remi',
    'size': 53,
    'nb_rows': 4,
    'nb_profiled_rows': 4,
    'columns': [
        {
            'name': 'name',
            'structural_type': 'http://schema.org/Text',
            'semantic_types': [],
            'num_distinct_values': 4,
        },
        {
            'name': 'age',
            'structural_type': 'http://schema.org/Integer',
            'semantic_types': [],
            'unclean_values_ratio': 0.0,
            'num_distinct_values': 4,
            'mean': lambda n: round(n, 2) == 26.0,
            'stddev': lambda n: round(n, 2) == 10.61,
            'coverage': (
                lambda l: sorted(l, key=lambda e: e['range']['gte']) == [
                    {
                        'range': {
                            'gte': 9.0,
                            'lte': 9.0,
                        },
                    },
                    {
                        'range': {
                            'gte': 27.0,
                            'lte': 30.0,
                        },
                    },
                    {
                        'range': {
                            'gte': 38.0,
                            'lte': 38.0,
                        },
                    },
                ]
            ),
            'plot': check_plot('histogram_numerical'),
        },
    ],
    'materialize': {
        'direct_url': lambda v: isinstance(v, str),
        'identifier': 'datamart.test',
        'date': lambda d: isinstance(d, str),
        'convert': [{'identifier': fmt}],
    },
    'sample': 'name,age\r\nC++,38.0\r\nPython,30.0\r\nRust,9.0\r\nLua,27.0\r' +
              '\n',
    'date': lambda d: isinstance(d, str),
    'version': version
}
