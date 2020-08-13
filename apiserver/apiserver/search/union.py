import distance
import logging

from .base import get_column_identifiers


logger = logging.getLogger(__name__)


PAGINATION_SIZE = 200


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
        meta = es.get('datamart', dt)['_source']
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
