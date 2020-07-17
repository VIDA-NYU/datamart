from datetime import datetime
import distance
import hashlib
import json
import logging
import os
import tempfile
import textwrap
import time

import tornado.web

from datamart_core import types
from datamart_core.fscache import cache_get_or_set
from datamart_core.materialize import detect_format_convert_to_csv
from datamart_profiler import process_dataset, parse_date
from datamart_profiler.temporal import temporal_aggregation_keys


logger = logging.getLogger(__name__)


PAGINATION_SIZE = 200
TOP_K_SIZE = 50


def compute_levenshtein_sim(str1, str2):
    """
    Computer the Levenshtein Similarity between two strings using 3-grams, if one string
    is not contained in the other.
    """

    if str1 in str2 or str2 in str1:
        return 1

    if len(str1) < 3:
        str1_set = [str1]
    else:
        str1_set = [str1[i:i + 3] for i in range(len(str1) - 2)]

    if len(str2) < 3:
        str2_set = [str2]
    else:
        str2_set = [str2[i:i + 3] for i in range(len(str2) - 2)]

    return 1 - distance.nlevenshtein(str1_set, str2_set, method=2)


class ClientError(ValueError):
    """Error in query sent by client.
    """


temporal_resolutions_priorities = {
    n: i
    for i, n in enumerate(reversed(list(temporal_aggregation_keys)))
}


def get_column_coverage(data_profile, filter_=()):
    """
    Get coverage for each column of the input dataset.

    :param data_profile: Profiled input dataset, if dataset is not in Datamart index.
    :param filter_: list of column indices to return. If an empty list, return all the columns.
    :return: dict, where key is the column index, and value is a dict as follows:

        {
            'type': column meta-type ('structural_type', 'semantic_types', 'spatial'),
            'type_value': column type,
            'ranges': list of ranges
        }
    """

    column_index_mapping = {
        column['name']: idx
        for idx, column in enumerate(data_profile['columns'])
    }

    column_coverage = dict()

    for column_index, column in enumerate(data_profile['columns']):
        column_name = column['name']
        if 'coverage' not in column:
            continue
        if filter_ and column_index not in filter_:
            continue
        # ignoring 'd3mIndex'
        if column_name == 'd3mIndex':
            continue
        if types.ID in column['semantic_types']:
            type_ = 'semantic_types'
            type_value = types.ID
        elif column['structural_type'] == types.INTEGER:
            type_ = 'structural_type'
            type_value = column['structural_type']
        elif types.DATE_TIME in column['semantic_types']:
            type_ = 'semantic_types'
            type_value = types.DATE_TIME
        else:
            continue
        column_coverage[(column_index,)] = {
            'type': type_,
            'type_value': type_value,
            'ranges': [],
        }
        for range_ in column['coverage']:
            column_coverage[(column_index,)]['ranges'].append([
                float(range_['range']['gte']),
                float(range_['range']['lte']),
            ])

    if 'spatial_coverage' in data_profile:
        for spatial in data_profile['spatial_coverage']:
            # Keep in sync, search code for 279a32
            if 'lat' in spatial:
                if (
                    filter_ and (
                        column_index_mapping[spatial['lat']] not in filter_ or
                        column_index_mapping[spatial['lon']] not in filter_
                    )
                ):
                    continue
                names = (column_index_mapping[spatial['lat']],
                         column_index_mapping[spatial['lon']])
            elif 'address' in spatial:
                if (
                    filter_ and
                    column_index_mapping[spatial['address']] not in filter_
                ):
                    continue
                names = (column_index_mapping[spatial['address']],)
            elif 'point' in spatial:
                if (
                    filter_ and
                    column_index_mapping[spatial['point']] not in filter_
                ):
                    continue
                names = (column_index_mapping[spatial['point']],)
            elif 'admin' in spatial:
                if (
                    filter_ and
                    column_index_mapping[spatial['admin']] not in filter_
                ):
                    continue
                names = (column_index_mapping[spatial['admin']],)
            else:
                raise ValueError("Invalid spatial_coverage")
            column_coverage[names] = {
                'type': 'spatial',
                'type_value': types.LATITUDE + ',' + types.LONGITUDE,
                'ranges': []
            }
            for range_ in spatial['ranges']:
                column_coverage[names]['ranges'].append(
                    range_['range']['coordinates']
                )

    return column_coverage


JOIN_RESULT_SOURCE_FIELDS = [
    # General
    'dataset_id', 'name',
    # Column indices
    'index',
    'lat_index', 'lon_index', 'lat', 'lon',
    'address_index', 'address',
    # To determine temporal resolution of join
    'temporal_resolution',
]


def get_lazo_sketches(data_profile, filter_=None):
    """
    Get Lazo sketches of the input dataset, if available.

    :param data_profile: Profiled input dataset.
    :param filter_: list of column indices to return.
       If an empty list, return all the columns.
    :return: dict, where key is the column index, and value is a tuple
        (n_permutations, hash_values, cardinality)
    """

    lazo_sketches = dict()

    for column_index, column in enumerate(data_profile['columns']):
        if 'lazo' in column:
            if not filter_ or column_index in filter_:
                lazo_sketches[(column_index,)] = (
                    column['lazo']['n_permutations'],
                    column['lazo']['hash_values'],
                    column['lazo']['cardinality'],
                )

    return lazo_sketches


def get_numerical_join_search_results(
    es, type_, type_value, pivot_column, ranges, dataset_id=None, ignore_datasets=None,
    query_sup_functions=None, query_sup_filters=None,
):
    """Retrieve numerical join search results that intersect with the input numerical ranges.
    """

    filter_query = []
    must_not_query = []
    if query_sup_filters:
        filter_query.extend(query_sup_filters)
    filter_query.append({'term': {'%s' % type_: type_value}})
    if dataset_id:
        filter_query.append(
            {'term': {'dataset_id': dataset_id}}
        )
    if type_value != types.DATE_TIME:
        filter_query.append(
            {'fuzzy': {'name.raw': pivot_column}}
        )
    if ignore_datasets:
        must_not_query.extend(
            {'term': {'dataset_id': id}}
            for id in ignore_datasets
        )

    should_query = list()
    coverage = sum([range_[1] - range_[0] + 1 for range_ in ranges])
    for i, range_ in enumerate(ranges):
        should_query.append({
            'nested': {
                'path': 'coverage',
                'query': {
                    'function_score': {
                        'query': {
                            'range': {
                                'coverage.range': {
                                    'gte': range_[0],
                                    'lte': range_[1],
                                    'relation': 'intersects'
                                }
                            }
                        },
                        'script_score': {
                            'script': {
                                'params': {
                                    'gte': range_[0],
                                    'lte': range_[1],
                                    'coverage': coverage
                                },
                                'source': textwrap.dedent('''\
                                    double start = Math.max(
                                        params.gte,
                                        doc['coverage.gte'].value
                                    );
                                    double end = Math.min(
                                        params.lte,
                                        doc['coverage.lte'].value
                                    );
                                    return (end - start + 1) / params.coverage;
                                ''')
                            }
                        },
                        'boost_mode': 'replace'
                    }
                },
                'inner_hits': {
                    '_source': False,
                    'size': 100,
                    'name': 'range-{0}'.format(i)
                },
                'score_mode': 'sum'
            }
        })

    body = {
        '_source': {
            'includes': JOIN_RESULT_SOURCE_FIELDS
        },
        'query': {
            'function_score': {
                'query': {
                    'bool': {
                        'filter': filter_query,
                        'should': should_query,
                        'must_not': must_not_query,
                        'minimum_should_match': 1
                    }
                },
                'functions': query_sup_functions or [],
                'score_mode': 'sum',
                'boost_mode': 'multiply'
            }
        }
    }

    return es.search(
        index='datamart_columns',
        body=body,
        size=TOP_K_SIZE
    )['hits']['hits']


def get_spatial_join_search_results(
    es, ranges, dataset_id=None, ignore_datasets=None,
    query_sup_functions=None, query_sup_filters=None,
):
    """Retrieve spatial join search results that intersect
    with the input spatial ranges.
    """

    filter_query = []
    must_not_query = []
    if query_sup_filters:
        filter_query.extend(query_sup_filters)
    if dataset_id:
        filter_query.append(
            {'term': {'dataset_id': dataset_id}}
        )
    if ignore_datasets:
        must_not_query.extend(
            {'term': {'dataset_id': id}}
            for id in ignore_datasets
        )

    should_query = list()
    coverage = sum([
        (range_[1][0] - range_[0][0]) * (range_[0][1] - range_[1][1])
        for range_ in ranges])
    for i, range_ in enumerate(ranges):
        should_query.append({
            'nested': {
                'path': 'ranges',
                'query': {
                    'function_score': {
                        'query': {
                            'geo_shape': {
                                'ranges.range': {
                                    'shape': {
                                        'type': 'envelope',
                                        'coordinates': [
                                            [range_[0][0], range_[0][1]],
                                            [range_[1][0], range_[1][1]]
                                        ]
                                    },
                                    'relation': 'intersects'
                                }
                            }
                        },
                        'script_score': {
                            'script': {
                                'params': {
                                    'min_lon': range_[0][0],
                                    'max_lat': range_[0][1],
                                    'max_lon': range_[1][0],
                                    'min_lat': range_[1][1],
                                    'coverage': coverage
                                },
                                'source': textwrap.dedent('''\
                                    double n_min_lon = Math.max(doc['ranges.min_lon'].value, params.min_lon);
                                    double n_max_lat = Math.min(doc['ranges.max_lat'].value, params.max_lat);
                                    double n_max_lon = Math.min(doc['ranges.max_lon'].value, params.max_lon);
                                    double n_min_lat = Math.max(doc['ranges.min_lat'].value, params.min_lat);
                                    return ((n_max_lon - n_min_lon) * (n_max_lat - n_min_lat)) / params.coverage;
                                ''')
                            }
                        },
                        'boost_mode': 'replace'
                    }
                },
                'inner_hits': {
                    '_source': False,
                    'size': 100,
                    'name': 'range-{0}'.format(i)
                },
                'score_mode': 'sum'
            }
        })

    body = {
        '_source': {
            'includes': JOIN_RESULT_SOURCE_FIELDS
        },
        'query': {
            'function_score': {
                'query': {
                    'bool': {
                        'filter': filter_query,
                        'should': should_query,
                        'must_not': must_not_query,
                        'minimum_should_match': 1,
                    }
                },
                'functions': query_sup_functions or [],
                'score_mode': 'sum',
                'boost_mode': 'multiply'
            }
        }
    }

    return es.search(
        index='datamart_spatial_coverage',
        body=body,
        size=TOP_K_SIZE
    )['hits']['hits']


def get_textual_join_search_results(
    es, query_results,
    query_sup_functions=None, query_sup_filters=None,
):
    """Combine Lazo textual search results with Elasticsearch
    (keyword search).
    """

    scores_per_dataset = dict()
    column_per_dataset = dict()
    for d_id, name, lazo_score in query_results:
        if d_id not in column_per_dataset:
            column_per_dataset[d_id] = list()
            scores_per_dataset[d_id] = dict()
        column_per_dataset[d_id].append(name)
        scores_per_dataset[d_id][name] = lazo_score

    # if there is no keyword query
    if not (query_sup_functions or query_sup_filters):
        results = list()
        for dataset_id in column_per_dataset:
            column_indices = get_column_identifiers(
                es=es,
                column_names=column_per_dataset[dataset_id],
                dataset_id=dataset_id
            )
            for j in range(len(column_indices)):
                column_name = column_per_dataset[dataset_id][j]
                results.append(
                    dict(
                        _score=scores_per_dataset[dataset_id][column_name],
                        _source=dict(
                            dataset_id=dataset_id,
                            name=column_name,
                            index=column_indices[j]
                        )
                    )
                )
        return results

    # if there is a keyword query
    should_query = list()
    for d_id, name, lazo_score in query_results:
        should_query.append(
            {
                'constant_score': {
                    'filter': {
                        'bool': {
                            'must': [
                                {
                                    'term': {
                                        'dataset_id': d_id
                                    }
                                },
                                {
                                    'term': {
                                        'name.raw': name
                                    }
                                }
                            ]
                        }
                    },
                    'boost': lazo_score
                }
            }
        )

    body = {
        '_source': {
            'includes': JOIN_RESULT_SOURCE_FIELDS
        },
        'query': {
            'function_score': {
                'query': {
                    'bool': {
                        'filter': query_sup_filters or [],
                        'should': should_query,
                        'minimum_should_match': 1
                    }
                },
                'functions': query_sup_functions,
                'score_mode': 'sum',
                'boost_mode': 'multiply'
            }
        }
    }

    return es.search(
        index='datamart_columns',
        body=body,
        size=TOP_K_SIZE
    )['hits']['hits']


def get_column_identifiers(es, column_names, dataset_id=None, data_profile=None):
    column_indices = [-1 for _ in column_names]
    if not data_profile:
        columns = es.get('datamart', dataset_id, _source='columns.name')
        columns = columns['_source']['columns']
    else:
        columns = data_profile['columns']
    for i in range(len(columns)):
        for j in range(len(column_names)):
            if columns[i]['name'] == column_names[j]:
                column_indices[j] = i
    return column_indices


def get_dataset_metadata(es, dataset_id):
    """
    Retrieve metadata about input dataset.

    """

    hit = es.get('datamart', dataset_id)

    return hit


def get_joinable_datasets(
    es, lazo_client, data_profile, dataset_id=None, ignore_datasets=None,
    query_sup_functions=None, query_sup_filters=None,
    tabular_variables=(),
):
    """
    Retrieve datasets that can be joined with an input dataset.

    :param es: Elasticsearch client.
    :param lazo_client: client for the Lazo Index Server
    :param data_profile: Profiled input dataset.
    :param dataset_id: The identifier of the desired Datamart dataset for augmentation.
    :param ignore_datasets: Identifiers of datasets to ignore.
    :param query_sup_functions: list of query functions over sup index.
    :param query_sup_filters: list of query filters over sup index.
    :param tabular_variables: specifies which columns to focus on for the search.
    """

    # get the coverage for each column of the input dataset
    column_coverage = get_column_coverage(
        data_profile,
        tabular_variables,
    )

    # search results
    search_results = list()

    # numerical, temporal, and spatial attributes
    for column, coverage in column_coverage.items():
        type_ = coverage['type']
        type_value = coverage['type_value']
        if type_ == 'spatial':
            spatial_results = get_spatial_join_search_results(
                es,
                coverage['ranges'],
                dataset_id,
                ignore_datasets,
                query_sup_functions,
                query_sup_filters,
            )
            for result in spatial_results:
                result['companion_column'] = column
                search_results.append(result)
        elif len(column) == 1:
            column_name = data_profile['columns'][column[0]]['name']
            numerical_results = get_numerical_join_search_results(
                es,
                type_,
                type_value,
                column_name,
                coverage['ranges'],
                dataset_id,
                ignore_datasets,
                query_sup_functions,
                query_sup_filters,
            )
            for result in numerical_results:
                result['companion_column'] = column
                search_results.append(result)
        else:
            raise ValueError("Non-spatial coverage from multiple columns?")

    # textual/categorical attributes
    lazo_sketches = get_lazo_sketches(
        data_profile,
        tabular_variables,
    )
    for column, (n_permutations, hash_values, cardinality) in lazo_sketches.items():
        query_results = lazo_client.query_lazo_sketch_data(
            n_permutations,
            hash_values,
            cardinality
        )
        if dataset_id:
            query_results = [
                res for res in query_results if res[0] == dataset_id
            ]
        if ignore_datasets:
            query_results = [
                res for res in query_results if res[0] not in ignore_datasets
            ]
        if not query_results:
            continue
        textual_results = get_textual_join_search_results(
            es,
            query_results,
            query_sup_functions,
            query_sup_filters,
        )
        for result in textual_results:
            result['companion_column'] = column
            search_results.append(result)

    search_results = sorted(
        search_results,
        key=lambda item: item['_score'],
        reverse=True
    )

    results = []
    for result in search_results:
        dt = result['_source']['dataset_id']
        info = get_dataset_metadata(es, dt)
        meta = info.pop('_source')
        left_columns = []
        right_columns = []
        left_columns_names = []
        right_columns_names = []
        left_temporal_resolution = None
        right_temporal_resolution = None

        left_columns.append(list(result['companion_column']))
        left_columns_names.append([
            data_profile['columns'][comp]['name']
            for comp in result['companion_column']
        ])
        for left_indices in left_columns:
            if len(left_indices) == 1:
                left_index = left_indices[0]
                column = data_profile['columns'][left_index]
                if 'temporal_resolution' in column:
                    left_temporal_resolution = column['temporal_resolution']
                    break

        source = result['_source']
        # Keep in sync, search code for 279a32
        if 'index' in source:
            right_columns.append([source['index']])
            right_columns_names.append([source['name']])
            right_temporal_resolution = source.get('temporal_resolution')
        elif 'lat_index' in source and 'lon_index' in source:
            right_columns.append([
                source['lat_index'],
                source['lon_index'],
            ])
            right_columns_names.append([
                source['lat'],
                source['lon'],
            ])
        elif 'address_index' in source:
            right_columns.append([
                source['address_index'],
            ])
            right_columns_names.append([
                source['address'],
            ])
        elif 'point_index' in source:
            right_columns.append([
                source['point_index'],
            ])
            right_columns_names.append([
                source['point'],
            ])
        elif 'admin_index' in source:
            right_columns.append([
                source['admin_index'],
            ])
            right_columns_names.append([
                source['admin'],
            ])
        else:
            logger.error("Invalid spatial_coverage")
            continue

        res = dict(
            id=dt,
            score=result['_score'],
            metadata=meta,
            augmentation={
                'type': 'join',
                'left_columns': left_columns,
                'right_columns': right_columns,
                'left_columns_names': left_columns_names,
                'right_columns_names': right_columns_names,
            }
        )
        logger.info(
            "Temporal resolutions: left=%r right=%r",
            left_temporal_resolution,
            right_temporal_resolution,
        )
        if left_temporal_resolution and right_temporal_resolution:
            # Keep in sync with lib_augmentation's match_column_temporal_resolutions
            if (
                temporal_resolutions_priorities[left_temporal_resolution] >
                temporal_resolutions_priorities[right_temporal_resolution]
            ):
                join_resolution = left_temporal_resolution
            else:
                join_resolution = right_temporal_resolution
            res['augmentation']['temporal_resolution'] = join_resolution
        results.append(res)

    return results


def get_columns_by_type(data_profile, filter_=()):
    """
    Retrieve a mapping of types to column names for a dataset.
    """

    output = dict()
    for column_index, column in enumerate(data_profile['columns']):
        name = column['name']
        if filter_ and column_index not in filter_:
            continue
        # ignoring 'd3mIndex'
        if name == 'd3mIndex':
            continue
        semantic_types = column['semantic_types']
        for semantic_type in semantic_types:
            if semantic_type not in output:
                output[semantic_type] = []
            output[semantic_type].append(name)
        if not semantic_types:
            if column['structural_type'] not in output:
                output[column['structural_type']] = []
            output[column['structural_type']].append(name)
    return output


def get_unionable_datasets(es, data_profile, dataset_id=None, ignore_datasets=None,
                           query_args_main=None, tabular_variables=()):
    """
    Retrieve datasets that can be unioned to an input dataset using fuzzy search
    (max edit distance = 2).

    :param es: Elasticsearch client.
    :param data_profile: Profiled input dataset.
    :param dataset_id: The identifier of the desired Datamart dataset for augmentation.
    :param ignore_datasets: Identifiers of datasets to ignore.
    :param query_args_main: list of query arguments (optional).
    :param tabular_variables: specifies which columns to focus on for the search.
    """

    main_dataset_columns = get_columns_by_type(
        data_profile=data_profile,
        filter_=tabular_variables
    )

    n_columns = 0
    for type_ in main_dataset_columns:
        n_columns += len(main_dataset_columns[type_])

    column_pairs = dict()
    for type_ in main_dataset_columns:
        for att in main_dataset_columns[type_]:
            partial_query = {
                'should': [
                    {
                        'term': {'columns.structural_type': type_}
                    },
                    {
                        'term': {'columns.semantic_types': type_}
                    },
                ],
                'must': [
                    {
                        'fuzzy': {'columns.name.raw': att}
                    }
                ],
                'minimum_should_match': 1
            }

            if dataset_id:
                partial_query['must'].append(
                    {'term': {'_id': dataset_id}}
                )
            if ignore_datasets:
                partial_query.setdefault('must_not', []).extend(
                    {'term': {'_id': id}}
                    for id in ignore_datasets
                )

            query = {
                'nested': {
                    'path': 'columns',
                    'query': {
                        'bool': partial_query
                    },
                    'inner_hits': {'_source': False, 'size': 100}
                }
            }

            if not query_args_main:
                args = query
            else:
                args = [query] + query_args_main
            query_obj = {
                '_source': {
                    'includes': [
                        'columns.name',
                    ],
                },
                'query': {
                    'bool': {
                        'must': args,
                    }
                }
            }

            # FIXME: Use search-after API here?
            from_ = 0
            while True:
                hits = es.search(
                    index='datamart',
                    body=query_obj,
                    from_=from_,
                    size=PAGINATION_SIZE,
                    request_timeout=30
                )['hits']['hits']
                from_ += len(hits)

                for hit in hits:

                    dataset_name = hit['_id']
                    es_score = hit['_score'] if query_args_main else 1
                    columns = hit['_source']['columns']
                    inner_hits = hit['inner_hits']

                    if dataset_name not in column_pairs:
                        column_pairs[dataset_name] = []

                    for column_hit in inner_hits['columns']['hits']['hits']:
                        column_offset = int(column_hit['_nested']['offset'])
                        column_name = columns[column_offset]['name']
                        sim = compute_levenshtein_sim(att.lower(), column_name.lower())
                        column_pairs[dataset_name].append((att, column_name, sim, es_score))

                if len(hits) != PAGINATION_SIZE:
                    break

    scores = dict()
    for dataset in list(column_pairs.keys()):

        # choose pairs with higher similarity
        seen_1 = set()
        seen_2 = set()
        pairs = []
        for att_1, att_2, sim, es_score in sorted(column_pairs[dataset],
                                                  key=lambda item: item[2],
                                                  reverse=True):
            if att_1 in seen_1 or att_2 in seen_2:
                continue
            seen_1.add(att_1)
            seen_2.add(att_2)
            pairs.append((att_1, att_2, sim, es_score))

        if len(pairs) <= 1:
            del column_pairs[dataset]
            continue

        column_pairs[dataset] = pairs
        scores[dataset] = 0
        es_score = 0

        for pair in column_pairs[dataset]:
            sim = pair[2]
            scores[dataset] += sim
            es_score = max(es_score, pair[3])

        scores[dataset] = (scores[dataset] / n_columns) * es_score

    sorted_datasets = sorted(
        scores.items(),
        key=lambda item: item[1],
        reverse=True
    )

    results = []
    for dt, score in sorted_datasets:
        info = get_dataset_metadata(es, dt)
        meta = info.pop('_source')
        # TODO: augmentation information is incorrect
        left_columns = []
        right_columns = []
        left_columns_names = []
        right_columns_names = []
        for att_1, att_2, sim, es_score in column_pairs[dt]:
            if dataset_id:
                left_columns.append(
                    get_column_identifiers(es, [att_1], dataset_id=dataset_id)
                )
            else:
                left_columns.append(
                    get_column_identifiers(es, [att_1], data_profile=data_profile)
                )
            left_columns_names.append([att_1])
            right_columns.append(
                get_column_identifiers(es, [att_2], dataset_id=dt)
            )
            right_columns_names.append([att_2])
        results.append(dict(
            id=dt,
            score=score,
            metadata=meta,
            augmentation={
                'type': 'union',
                'left_columns': left_columns,
                'right_columns': right_columns,
                'left_columns_names': left_columns_names,
                'right_columns_names': right_columns_names
            }
        ))

    return results


def parse_keyword_query_main_index(query_json):
    """Parses a Datamart keyword query, turning it into an
    Elasticsearch query over 'datamart' index.
    """

    query_args_main = list()
    if query_json.get('keywords'):
        keywords = query_json['keywords']
        if isinstance(keywords, list):
            keywords = ' '.join(keywords)
        query_args_main.append({
            'bool': {
                'should': [
                    {
                        'multi_match': {
                            'query': keywords,
                            'operator': 'or',
                            'type': 'most_fields',
                            'fields': [
                                'id',
                                'description',
                                'name',
                            ],
                        },
                    },
                    {
                        'nested': {
                            'path': 'columns',
                            'query': {
                                'multi_match': {
                                    'query': keywords,
                                    'operator': 'or',
                                    'type': 'most_fields',
                                    'fields': [
                                        'columns.name',
                                    ],
                                },
                            },
                        },
                    },
                ]
            },
        })

    if 'source' in query_json:
        source = query_json['source']
        if not isinstance(source, list):
            source = [source]
        query_args_main.append({
            'bool': {
                'filter': [
                    {
                        'terms': {
                            'source': source,
                        }
                    }
                ]
            }
        })

    return query_args_main


def parse_keyword_query_sup_index(query_json):
    """Parses a Datamart keyword query, turning it into an
    Elasticsearch query over 'datamart_column' and
    'datamart_spatial_coverage' indices.
    """
    query_sup_functions = list()
    query_sup_filters = list()

    if query_json.get('keywords'):
        keywords = query_json['keywords']
        if isinstance(keywords, list):
            keywords = ' '.join(keywords)
        query_sup_functions.append({
            'filter': {
                'multi_match': {
                    'query': keywords,
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
        })

    if 'source' in query_json:
        query_sup_filters.append({
            'terms': {
                'dataset_source': query_json['source'],
            }
        })

    return query_sup_functions, query_sup_filters


def parse_query(query_json):
    """Parses a Datamart query, turning it into an Elasticsearch query
    over 'datamart' index as well as the supplementary indices
    ('datamart_columns' and 'datamart_spatial_coverage').
    """

    query_args_main = parse_keyword_query_main_index(query_json)
    query_sup_functions, query_sup_filters = \
        parse_keyword_query_sup_index(query_json)

    # tabular_variables
    tabular_variables = []

    # variables
    variables_query = None
    if 'variables' in query_json:
        variables_query, tabular_variables = parse_query_variables(
            query_json['variables']
        )

    # TODO: for now, temporal and geospatial variables are ignored
    #   for 'datamart_columns' and 'datamart_spatial_coverage' indices,
    #   since we do not have information about a dataset in these indices
    if variables_query:
        query_args_main.extend(variables_query)

    return query_args_main, query_sup_functions, query_sup_filters, list(set(tabular_variables))


def parse_query_variables(data):
    """Parses the variables of a Datamart query, turning it into an
    Elasticsearch query over 'datamart' index
    """

    output = list()
    tabular_variables = []

    if not data:
        return output, tabular_variables

    for variable in data:
        if 'type' not in variable:
            raise ClientError("variable is missing property 'type'")

        # temporal variable
        if variable['type'] == 'temporal_variable':
            filters = [
                {
                    'term': {
                        'columns.semantic_types': types.DATE_TIME,
                    },
                }
            ]
            if 'start' in variable or 'end' in variable:
                if 'start' in variable:
                    start = parse_date(variable['start'])
                    if start is None:
                        raise ClientError("Invalid start date format")
                    start = start.timestamp()
                else:
                    start = 0

                if 'end' in variable:
                    end = parse_date(variable['end'])
                    if end is None:
                        raise ClientError("Invalid end date format")
                    end = end.timestamp()
                else:
                    end = datetime.utcnow().timestamp()
                    if start > end:
                        end = start + 1

                if start > end:
                    raise ClientError("Invalid date range (start > end)")

                filters.append({
                    'nested': {
                        'path': 'columns.coverage',
                        'query': {
                            'range': {
                                'columns.coverage.range': {
                                    'gte': start,
                                    'lte': end,
                                    'relation': 'intersects',
                                },
                            },
                        },
                    },
                })
            if 'granularity' in variable:
                granularity = variable['granularity']

                filters.append({
                    'term': {
                        'columns.temporal_resolution': granularity,
                    },
                })

            output.append({
                'nested': {
                    'path': 'columns',
                    'query': {
                        'bool': {
                            'must': filters,
                        },
                    },
                },
            })

        # geospatial variable
        # TODO: handle 'granularity'
        elif variable['type'] == 'geospatial_variable':
            if (
                'latitude1' not in variable or
                'latitude2' not in variable or
                'longitude1' not in variable or
                'longitude2' not in variable
            ):
                continue
            longitude1 = min(
                float(variable['longitude1']),
                float(variable['longitude2'])
            )
            longitude2 = max(
                float(variable['longitude1']),
                float(variable['longitude2'])
            )
            latitude1 = max(
                float(variable['latitude1']),
                float(variable['latitude2'])
            )
            latitude2 = min(
                float(variable['latitude1']),
                float(variable['latitude2'])
            )
            output.append({
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
                                                [longitude1, latitude1],
                                                [longitude2, latitude2],
                                            ],
                                        },
                                        'relation': 'intersects'
                                    }
                                }
                            }
                        }
                    }
                }
            })

        # tabular variable
        # TODO: handle 'relationship'
        #  for now, it assumes the relationship is 'contains'
        elif variable['type'] == 'tabular_variable':
            if 'columns' in variable:
                for column_index in variable['columns']:
                    tabular_variables.append(column_index)

    return output, tabular_variables


def get_augmentation_search_results(
    es, lazo_client, data_profile,
    query_args_main, query_sup_functions, query_sup_filters,
    tabular_variables,
    dataset_id=None, join=True, union=True, ignore_datasets=None,
):
    join_results = []
    union_results = []

    if join:
        logger.info("Looking for joins...")
        start = time.perf_counter()
        join_results = get_joinable_datasets(
            es=es,
            lazo_client=lazo_client,
            data_profile=data_profile,
            dataset_id=dataset_id,
            ignore_datasets=ignore_datasets,
            query_sup_functions=query_sup_functions,
            query_sup_filters=query_sup_filters,
            tabular_variables=tabular_variables,
        )
        logger.info("Found %d join results in %.2fs",
                    len(join_results), time.perf_counter() - start)
    if union:
        logger.info("Looking for unions...")
        start = time.perf_counter()
        union_results = get_unionable_datasets(
            es=es,
            data_profile=data_profile,
            dataset_id=dataset_id,
            ignore_datasets=ignore_datasets,
            query_args_main=query_args_main,
            tabular_variables=tabular_variables,
        )
        logger.info("Found %d union results in %.2fs",
                    len(union_results), time.perf_counter() - start)

    min_size = min(len(join_results), len(union_results))
    results = list(zip(join_results[:min_size], union_results[:min_size]))
    results = [elt for sublist in results for elt in sublist]

    if len(join_results) > min_size:
        results += join_results[min_size:]
    if len(union_results) > min_size:
        results += union_results[min_size:]

    for result in results:
        result['supplied_id'] = None
        result['supplied_resource_id'] = None

    return results[:TOP_K_SIZE]  # top-50


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

                self.application.redis.set(
                    'profile:' + data_hash,
                    json.dumps(
                        data_profile,
                        # Compact
                        sort_keys=True, indent=None, separators=(',', ':'),
                    ),
                )

        return data_profile, data_hash
