import unittest

from apiserver.search import parse_query


class TestSearch(unittest.TestCase):
    def test_simple(self):
        main, sup_funcs, sup_filters, vars = parse_query({
            'keywords': ['green', 'taxi'],
            'source': 'gov',
        })
        self.assertEqual(
            main,
            [
                {
                    'bool': {
                        'should': [
                            {
                                'multi_match': {
                                    'query': 'green taxi',
                                    'operator': 'or',
                                    'type': 'most_fields',
                                    'fields': ['id', 'description', 'name'],
                                },
                            },
                            {
                                'nested': {
                                    'path': 'columns',
                                    'query': {
                                        'multi_match': {
                                            'query': 'green taxi',
                                            'operator': 'or',
                                            'type': 'most_fields',
                                            'fields': ['columns.name'],
                                        },
                                    },
                                },
                            },
                        ],
                    },
                },
                {
                    'bool': {
                        'filter': [
                            {
                                'terms': {
                                    'source': ['gov'],
                                },
                            },
                        ],
                    },
                },
            ],
        )
        self.assertEqual(
            sup_funcs,
            [
                {
                    'filter': {
                        'multi_match': {
                            'query': 'green taxi',
                            'operator': 'or',
                            'type': 'most_fields',
                            'fields': [
                                'dataset_id',
                                'dataset_description',
                                'dataset_name',
                                'name',
                            ],
                        },
                    },
                    'weight': 10,
                },
            ],
        )
        self.assertEqual(
            sup_filters,
            [
                {
                    'terms': {
                        'dataset_source': 'gov',
                    },
                },
            ],
        )
        self.assertEqual(vars, [])

    def test_ranges(self):
        main, sup_funcs, sup_filters, vars = parse_query({
            'keywords': ['green', 'taxi'],
            'source': ['gov'],
            'variables': [
                {
                    'type': 'temporal_variable',
                    'start': '2019-01-01',
                    'end': '2019-12-31',
                },
                {
                    'type': 'geospatial_variable',
                    'latitude1': 45.4,
                    'latitude2': 50.6,
                    'longitude1': -73.2,
                    'longitude2': -75.8,
                },
            ],
        })
        self.assertEqual(
            main,
            [
                {
                    'bool': {
                        'should': [
                            {
                                'multi_match': {
                                    'query': 'green taxi',
                                    'operator': 'or',
                                    'type': 'most_fields',
                                    'fields': ['id', 'description', 'name'],
                                },
                            },
                            {
                                'nested': {
                                    'path': 'columns',
                                    'query': {
                                        'multi_match': {
                                            'query': 'green taxi',
                                            'operator': 'or',
                                            'type': 'most_fields',
                                            'fields': ['columns.name'],
                                        },
                                    },
                                },
                            },
                        ],
                    },
                },
                {
                    'bool': {
                        'filter': [
                            {
                                'terms': {
                                    'source': ['gov'],
                                },
                            },
                        ],
                    },
                },
                [
                    {
                        'nested': {
                            'path': 'columns',
                            'query': {
                                'bool': {
                                    'must': [
                                        {
                                            'term': {
                                                'columns.semantic_types': 'http://schema.org/DateTime',
                                            },
                                        },
                                        {
                                            'nested': {
                                                'path': 'columns.coverage',
                                                'query': {
                                                    'range': {
                                                        'columns.coverage.range': {
                                                            'gte': 1546300800.0,
                                                            'lte': 1577750400.0,
                                                            'relation': 'intersects',
                                                        },
                                                    },
                                                },
                                            },
                                        },
                                    ],
                                },
                            },
                        },
                    },
                    {
                        'nested': {
                            'path': 'spatial_coverage.ranges',
                            'query': {
                                'bool': {
                                    'filter': {
                                        'geo_shape': {
                                            'spatial_coverage.ranges.range': {
                                                'shape': {
                                                    'type': 'envelope',
                                                    'coordinates': [
                                                        [-75.8, 50.6],
                                                        [-73.2, 45.4],
                                                    ],
                                                },
                                                'relation': 'intersects',
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                ],
            ],
        )
