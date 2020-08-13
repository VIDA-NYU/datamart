import logging
import textwrap

from datamart_core import types
from datamart_profiler.temporal import temporal_aggregation_keys

from .base import TOP_K_SIZE, get_column_identifiers


logger = logging.getLogger(__name__)


temporal_resolutions_priorities = {
    n: i
    for i, n in enumerate(reversed(list(temporal_aggregation_keys)))
}


JOIN_RESULT_SOURCE_FIELDS = [
    # General
    'dataset_id', 'name',
    # Column indices
    # Keep in sync, search code for 279a32
    'index',
    'lat_index', 'lon_index', 'lat', 'lon',
    'address_index', 'address',
    'point_index', 'point',
    'admin_index', 'admin',
    # To determine temporal resolution of join
    'temporal_resolution',
]


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
        meta = es.get('datamart', dt)['_source']
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
