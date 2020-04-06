from datetime import datetime
from dateutil.parser import parse
import distance
import hashlib
import io
import logging
import pickle
import time
import tornado.web

from datamart_core import types
from datamart_profiler import process_dataset


logger = logging.getLogger(__name__)


BUF_SIZE = 128000
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


def get_column_coverage(data_profile, column_index_mapping, filter_=()):
    """
    Get coverage for each column of the input dataset.

    :param data_profile: Profiled input dataset, if dataset is not in Datamart index.
    :param column_index_mapping: mapping from column name to column index
    :param filter_: list of column indices to return. If an empty list, return all the columns.
    :return: dict, where key is the column index, and value is a dict as follows:

        {
            'type': column meta-type ('structural_type', 'semantic_types', 'spatial'),
            'type_value': column type,
            'ranges': list of ranges
        }
    """

    column_coverage = dict()

    for column in data_profile['columns']:
        column_name = column['name']
        column_index = column_index_mapping[column_name]
        if 'coverage' not in column:
            continue
        if filter_ and column_index not in filter_:
            continue
        # ignoring 'd3mIndex'
        if 'd3mIndex' in column_name:
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
        column_coverage[str(column_index)] = {
            'type':       type_,
            'type_value': type_value,
            'ranges':     []
        }
        for range_ in column['coverage']:
            column_coverage[str(column_index)]['ranges'].\
                append([float(range_['range']['gte']),
                        float(range_['range']['lte'])])

    if 'spatial_coverage' in data_profile:
        for spatial in data_profile['spatial_coverage']:
            if 'lat' in spatial:
                if (
                    filter_ and (
                        column_index_mapping[spatial['lat']] not in filter_ or
                        column_index_mapping[spatial['lon']] not in filter_
                    )
                ):
                    continue
                names = (str(column_index_mapping[spatial['lat']]) + ',' +
                         str(column_index_mapping[spatial['lon']]))
            elif 'address' in spatial:
                if (
                    filter_ and
                    column_index_mapping[spatial['address']] not in filter_
                ):
                    continue
                names = str(column_index_mapping[spatial['address']])
            else:
                raise ValueError("Invalid spatial_coverage")
            column_coverage[names] = {
                'type':      'spatial',
                'type_value': types.LATITUDE + ',' + types.LONGITUDE,
                'ranges':     []
            }
            for range_ in spatial['ranges']:
                column_coverage[names]['ranges'].\
                    append(range_['range']['coordinates'])

    return column_coverage


def get_lazo_sketches(data_profile, column_index_mapping, filter_=None):
    """
    Get Lazo sketches of the input dataset, if available.

    :param data_profile: Profiled input dataset.
    :param column_index_mapping: mapping from column name to column index
    :param filter_: list of column indices to return.
       If an empty list, return all the columns.
    :return: dict, where key is the column index, and value is a tuple
        (n_permutations, hash_values, cardinality)
    """

    lazo_sketches = dict()

    for column in data_profile['columns']:
        if 'lazo' in column:
            column_index = column_index_mapping[column['name']]
            if not filter_ or column_index in filter_:
                lazo_sketches[str(column_index)] = (
                    column['lazo']['n_permutations'],
                    column['lazo']['hash_values'],
                    column['lazo']['cardinality'],
                )

    return lazo_sketches


def get_numerical_join_search_results(es, type_, type_value, pivot_column, ranges,
                                      dataset_id=None, query_args=None):
    """Retrieve numerical join search results that intersect with the input numerical ranges.
    """

    filter_query = [{'term': {'%s' % type_: type_value}}]
    if dataset_id:
        filter_query.append(
            {'term': {'dataset_id': dataset_id}}
        )
    if type_value != types.DATE_TIME:
        filter_query.append(
            {'fuzzy': {'name.raw': pivot_column}}
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
                                'source': '''
                                double start = Math.max(params.gte, doc['coverage.gte'].value);
                                double end = Math.min(params.lte, doc['coverage.lte'].value);
                                return (end - start + 1) / params.coverage;'''
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
            'excludes': [
                'dataset_name',
                'dataset_description',
                'coverage',
                'mean',
                'stddev',
                'structural_type',
                'semantic_types'
            ]
        },
        'query': {
            'function_score': {
                'query': {
                    'bool': {
                        'filter': filter_query,
                        'should': should_query,
                        'minimum_should_match': 1
                    }
                },
                'functions': [] if not query_args else query_args,
                'score_mode': 'sum',
                'boost_mode': 'multiply'
            }
        }
    }

    # logger.info("Query (numerical): %r", body)

    return es.search(
        index='datamart_columns',
        body=body,
        size=TOP_K_SIZE
    )['hits']['hits']


def get_spatial_join_search_results(es, ranges, dataset_id=None,
                                    query_args=None):
    """Retrieve spatial join search results that intersect
    with the input spatial ranges.
    """

    filter_query = list()
    if dataset_id:
        filter_query.append(
            {'term': {'dataset_id': dataset_id}}
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
                                'source': '''
                                double n_min_lon = Math.max(doc['ranges.min_lon'].value, params.min_lon);
                                double n_max_lat = Math.min(doc['ranges.max_lat'].value, params.max_lat);
                                double n_max_lon = Math.min(doc['ranges.max_lon'].value, params.max_lon);
                                double n_min_lat = Math.max(doc['ranges.min_lat'].value, params.min_lat);
                                return ((n_max_lon - n_min_lon) * (n_max_lat - n_min_lat)) / params.coverage;'''
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
            'excludes': [
                'name',
                'dataset_name',
                'dataset_description',
                'ranges'
            ]
        },
        'query': {
            'function_score': {
                'query': {
                    'bool': {
                        'filter': filter_query,
                        'should': should_query,
                        'minimum_should_match': 1
                    }
                },
                'functions': [] if not query_args else query_args,
                'score_mode': 'sum',
                'boost_mode': 'multiply'
            }
        }
    }

    # logger.info("Query (spatial): %r", body)

    return es.search(
        index='datamart_spatial_coverage',
        body=body,
        size=TOP_K_SIZE
    )['hits']['hits']


def get_textual_join_search_results(es, dataset_ids, column_names,
                                    lazo_scores, query_args=None):
    """Combine Lazo textual search results with Elasticsearch
    (keyword search).
    """

    scores_per_dataset = dict()
    column_per_dataset = dict()
    for d_id, name, lazo_score in zip(dataset_ids, column_names, lazo_scores):
        if d_id not in column_per_dataset:
            column_per_dataset[d_id] = list()
            scores_per_dataset[d_id] = dict()
        column_per_dataset[d_id].append(name)
        scores_per_dataset[d_id][name] = lazo_score

    # if there is no keyword query
    if not query_args:
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
    for d_id, name, lazo_score in zip(dataset_ids, column_names, lazo_scores):
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
            'excludes': [
                'dataset_name',
                'dataset_description',
                'coverage',
                'mean',
                'stddev',
                'structural_type',
                'semantic_types'
            ]
        },
        'query': {
            'function_score': {
                'query': {
                    'bool': {
                        'should': should_query,
                        'minimum_should_match': 1
                    }
                },
                'functions': query_args,
                'score_mode': 'sum',
                'boost_mode': 'multiply'
            }
        }
    }

    # logger.info("Query (textual): %r", body)

    return es.search(
        index='datamart_columns',
        body=body,
        size=TOP_K_SIZE
    )['hits']['hits']


def get_column_identifiers(es, column_names, dataset_id=None, data_profile=None):
    column_indices = [-1 for _ in column_names]
    if not data_profile:
        columns = es.get('datamart', dataset_id)['_source']['columns']
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


def get_joinable_datasets(es, lazo_client, data_profile, dataset_id=None,
                          query_args=None, tabular_variables=()):
    """
    Retrieve datasets that can be joined with an input dataset.

    :param es: Elasticsearch client.
    :param lazo_client: client for the Lazo Index Server
    :param data_profile: Profiled input dataset.
    :param dataset_id: The identifier of the desired Datamart dataset for augmentation.
    :param query_args: list of query arguments (optional).
    :param tabular_variables: specifies which columns to focus on for the search.
    """

    if not dataset_id and not data_profile:
        raise TypeError("Either a dataset id or a data profile "
                        "must be provided for the join")

    column_index_mapping = {
        column['name']: idx
        for idx, column in enumerate(data_profile['columns'])
    }

    # get the coverage for each column of the input dataset

    column_coverage = get_column_coverage(
        data_profile,
        column_index_mapping,
        tabular_variables
    )

    # search results
    search_results = list()

    # numerical, temporal, and spatial attributes
    for column in column_coverage:
        type_ = column_coverage[column]['type']
        type_value = column_coverage[column]['type_value']
        if type_ == 'spatial':
            spatial_results = get_spatial_join_search_results(
                es,
                column_coverage[column]['ranges'],
                dataset_id,
                query_args
            )
            for result in spatial_results:
                result['companion_column'] = column
                search_results.append(result)
        else:
            column_name = data_profile['columns'][int(column)]['name']
            numerical_results = get_numerical_join_search_results(
                es,
                type_,
                type_value,
                column_name,
                column_coverage[column]['ranges'],
                dataset_id,
                query_args
            )
            for result in numerical_results:
                result['companion_column'] = column
                search_results.append(result)

    # textual/categorical attributes
    lazo_sketches = get_lazo_sketches(
        data_profile,
        column_index_mapping,
        tabular_variables
    )
    for column in lazo_sketches:
        n_permutations, hash_values, cardinality = lazo_sketches[column]
        query_results = lazo_client.query_lazo_sketch_data(
            n_permutations,
            hash_values,
            cardinality
        )
        if not query_results:
            continue
        dataset_ids = list()
        column_names = list()
        scores = list()
        for dataset_id, column_name, threshold in query_results:
            dataset_ids.append(dataset_id)
            column_names.append(column_name)
            scores.append(threshold)
        textual_results = get_textual_join_search_results(
            es,
            dataset_ids,
            column_names,
            scores,
            query_args
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
        # materialize = meta.get('materialize', {})
        if meta.get('description') and len(meta['description']) > 100:
            meta['description'] = meta['description'][:97] + "..."
        left_columns = []
        right_columns = []
        left_columns_names = []
        right_columns_names = []
        try:
            left_columns.append([int(result['companion_column'])])
            left_columns_names.append(
                [data_profile['columns'][int(result['companion_column'])]['name']]
            )
        except ValueError:
            index_1, index_2 = result['companion_column'].split(",")
            left_columns.append([int(index_1), int(index_2)])
            left_columns_names.append([data_profile['columns'][int(index_1)]['name'] +
                                       ', ' + data_profile['columns'][int(index_2)]['name']])
        source = result['_source']
        if 'index' in source:
            right_columns.append([source['index']])
            right_columns_names.append([source['name']])
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
        else:
            continue

        results.append(dict(
            id=dt,
            score=result['_score'],
            # discoverer=materialize['identifier'],
            metadata=meta,
            augmentation={
                'type': 'join',
                'left_columns': left_columns,
                'right_columns': right_columns,
                'left_columns_names': left_columns_names,
                'right_columns_names': right_columns_names
            }
        ))

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
        # ignoring phone numbers
        semantic_types = [
            sem for sem in column['semantic_types']
            if types.PHONE_NUMBER not in sem
        ]
        for semantic_type in semantic_types:
            if semantic_type not in output:
                output[semantic_type] = []
            output[semantic_type].append(name)
        if not semantic_types:
            if column['structural_type'] not in output:
                output[column['structural_type']] = []
            output[column['structural_type']].append(name)
    return output


def get_unionable_datasets(es, data_profile, dataset_id=None,
                           query_args=None, tabular_variables=()):
    """
    Retrieve datasets that can be unioned to an input dataset using fuzzy search
    (max edit distance = 2).

    :param es: Elasticsearch client.
    :param data_profile: Profiled input dataset.
    :param dataset_id: The identifier of the desired Datamart dataset for augmentation.
    :param query_args: list of query arguments (optional).
    :param tabular_variables: specifies which columns to focus on for the search.
    """

    if not dataset_id and not data_profile:
        raise TypeError("Either a dataset id or a data profile "
                        "must be provided for the union")

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

            query = {
                'nested': {
                    'path': 'columns',
                    'query': {
                        'bool': partial_query
                    },
                    'inner_hits': {'_source': False, 'size': 100}
                }
            }

            if not query_args:
                args = query
            else:
                args = [query] + query_args
            query_obj = {
                '_source': {
                    'excludes': [
                        'date',
                        'materialize',
                        'name',
                        'description',
                        'license',
                        'size',
                        'columns.mean',
                        'columns.stddev',
                        'columns.structural_type',
                        'columns.semantic_types'
                    ]
                },
                'query': {
                    'bool': {
                        'must': args,
                    }
                }
            }

            # logger.info("Query (union-fuzzy): %r", query_obj)

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
                    es_score = hit['_score'] if query_args else 1
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
        # materialize = meta.get('materialize', {})
        if meta.get('description') and len(meta['description']) > 100:
            meta['description'] = meta['description'][:97] + "..."
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
            # discoverer=materialize['identifier'],
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

    keywords_query_all = list()
    if 'keywords' in query_json and query_json['keywords']:
        if not isinstance(query_json['keywords'], list):
            raise ClientError("'keywords' must be an array")
        keywords_query = list()
        for name in query_json['keywords']:
            # description
            keywords_query.append({
                'match': {
                    'description': {
                        'query': name,
                        'operator': 'and'
                    }
                }
            })
            # name
            keywords_query.append({
                'match': {
                    'name': {
                        'query': name,
                        'operator': 'and'
                    }
                }
            })
            # keywords
            keywords_query.append({
                'nested': {
                    'path': 'columns',
                    'query': {
                        'match': {
                            'columns.name': {
                                'query': name,
                                'operator': 'and'
                            }
                        },
                    },
                },
            })
            keywords_query.append({
                'wildcard': {
                    'materialize.identifier': '*%s*' % name.lower()
                }
            })
        keywords_query_all.append({
            'bool': {
                'should': keywords_query,
                'minimum_should_match': 1
            }
        })

    return keywords_query_all


def parse_keyword_query_sup_index(query_json):
    """Parses a Datamart keyword query, turning it into an
    Elasticsearch query over 'datamart_column' and
    'datamart_spatial_coverage' indices.
    """

    keywords_query = list()
    if 'keywords' in query_json and query_json['keywords']:
        if not isinstance(query_json['keywords'], list):
            raise ClientError("'keywords' must be an array")
        for name in query_json['keywords']:
            # dataset description
            keywords_query.append({
                'filter': {
                    'match': {
                        'dataset_description': {
                            'query': name,
                            'operator': 'and'
                        }
                    }
                },
                'weight': 10
            })
            # dataset name
            keywords_query.append({
                'filter': {
                    'match': {
                        'dataset_name': {
                            'query': name,
                            'operator': 'and'
                        }
                    }
                },
                'weight': 10
            })
            # column name
            keywords_query.append({
                'filter': {
                    'match': {
                        'name': {
                            'query': name,
                            'operator': 'and'
                        }
                    }
                },
                'weight': 10
            })

    return keywords_query


def parse_query(query_json):
    """Parses a Datamart query, turning it into an Elasticsearch query
    over 'datamart' index as well as the supplementary indices
    ('datamart_columns' and 'datamart_spatial_coverage').
    """

    query_args_main = list()

    # keywords
    keywords_query_main = parse_keyword_query_main_index(query_json)
    query_args_sup = parse_keyword_query_sup_index(query_json)

    if keywords_query_main:
        query_args_main.append(keywords_query_main)

    # sources
    if 'source' in query_json:
        query_args_main.append({
            'bool': {
                'filter': [
                    {
                        'terms': {
                            'source': query_json['source'],
                        }
                    }
                ]
            }
        })

    # tabular_variables
    tabular_variables = []

    # variables
    variables_query = None
    if 'variables' in query_json:
        variables_query = parse_query_variables(
            query_json['variables'],
            tabular_variables=tabular_variables
        )

    # TODO: for now, temporal and geospatial variables are ignored
    #   for 'datamart_columns' and 'datamart_spatial_coverage' indices,
    #   since we do not have information about a dataset in these indices
    if variables_query:
        query_args_main.append(variables_query)

    return query_args_main, query_args_sup, list(set(tabular_variables))


def parse_query_variables(data, tabular_variables=None):
    """Parses the variables of a Datamart query, turning it into an
    Elasticsearch query over 'datamart' index
    """

    output = list()

    if not data:
        return output

    for variable in data:
        if 'type' not in variable:
            raise ClientError("variable is missing property 'type'")
        variable_query = list()

        # temporal variable
        # TODO: handle 'granularity'
        if 'temporal_variable' in variable['type']:
            variable_query.append({
                'nested': {
                    'path': 'columns',
                    'query': {
                        'term': {'columns.semantic_types': types.DATE_TIME},
                    },
                },
            })
            start = end = None
            if 'start' in variable and 'end' in variable:
                try:
                    start = parse(variable['start']).timestamp()
                    end = parse(variable['end']).timestamp()
                except (KeyError, ValueError, OverflowError):
                    pass
            elif 'start' in variable:
                try:
                    start = parse(variable['start']).timestamp()
                    end = datetime.now().timestamp()
                except (KeyError, ValueError, OverflowError):
                    pass
            elif 'end' in variable:
                try:
                    start = 0
                    end = parse(variable['end']).timestamp()
                except (KeyError, ValueError, OverflowError):
                    pass
            else:
                pass
            if start and end:
                variable_query.append({
                    'nested': {
                        'path': 'columns.coverage',
                        'query': {
                            'range': {
                                'columns.coverage.range': {
                                    'gte': start,
                                    'lte': end,
                                    'relation': 'intersects'
                                }
                            }
                        }
                    }
                })

        # geospatial variable
        # TODO: handle 'granularity'
        elif 'geospatial_variable' in variable['type']:
            if ('latitude1' not in variable or
                    'latitude2' not in variable or
                    'longitude1' not in variable or
                    'longitude2' not in variable):
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
            variable_query.append({
                'nested': {
                    'path': 'spatial_coverage.ranges',
                    'query': {
                        'bool': {
                            'filter': {
                                'geo_shape': {
                                    'spatial_coverage.ranges.range': {
                                        'shape': {
                                            'type': 'envelope',
                                            'coordinates':
                                                [[longitude1, latitude1],
                                                 [longitude2, latitude2]]
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
        elif 'tabular_variable' in variable['type']:
            if 'columns' in variable:
                for column_index in variable['columns']:
                    tabular_variables.append(column_index)

        if variable_query:
            output.append({
                'bool': {
                    'must': variable_query,
                }
            })

    if output:
        return {
            'bool': {
                'must': output
            }
        }
    return {}


def get_augmentation_search_results(es, lazo_client, data_profile,
                                    query_args_main, query_args_sup,
                                    tabular_variables, score_threshold,
                                    dataset_id=None, join=True, union=True):
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
            query_args=query_args_sup,
            tabular_variables=tabular_variables
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
            query_args=query_args_main,
            tabular_variables=tabular_variables
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

        data_profile = self.application.redis.get('profile_' + data_hash)

        if data_profile is not None:
            logger.info("Found cached profile_data")
            data_profile = pickle.loads(data_profile)
        else:
            logger.info("Profiling...")
            start = time.perf_counter()
            data_profile = process_dataset(
                data=io.BytesIO(data),
                lazo_client=self.application.lazo_client,
                nominatim=self.application.nominatim,
                search=True,
                include_sample=False,
                coverage=True,
            )
            logger.info("Profiled in %.2fs", time.perf_counter() - start)

            self.application.redis.set(
                'profile_' + data_hash,
                pickle.dumps(data_profile),
            )

        return data_profile, data_hash
