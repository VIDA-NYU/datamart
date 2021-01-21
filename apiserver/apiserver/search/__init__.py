import csv
import math
from datetime import datetime
import io
import json
import logging
import prometheus_client
import time

from datamart_core.prom import PromMeasureRequest
from datamart_profiler.temporal import parse_date, temporal_aggregation_keys

from ..base import BUCKETS, BaseHandler
from ..enhance_metadata import enhance_metadata
from ..graceful_shutdown import GracefulHandler
from ..profile import ProfilePostedData, get_data_profile_from_es, \
    profile_token_re
from .base import ClientError, TOP_K_SIZE
from .join import get_joinable_datasets
from .union import get_unionable_datasets


logger = logging.getLogger(__name__)


PROM_SEARCH = PromMeasureRequest(
    count=prometheus_client.Counter(
        'req_search_count',
        "Search requests",
    ),
    time=prometheus_client.Histogram(
        'req_search_seconds',
        "Search request time",
        buckets=BUCKETS,
    ),
)


def validate_str_list(value, what):
    """Validates that a value is either a string or a list of strings.

    Returns a list of strings (with possibly one element) in both cases.
    """
    if isinstance(value, str):
        return [value]
    elif (
        not isinstance(value, list)
        or not all(isinstance(elem, str) for elem in value)
    ):
        raise ClientError("Invalid %s" % what)
    else:
        return value


def parse_keyword_query_main_index(query_json):
    """Parses a Datamart keyword query, turning it into an
    Elasticsearch query over 'datamart' index.
    """

    query_args_main = list()
    if query_json.get('keywords'):
        keywords = ' '.join(
            validate_str_list(query_json['keywords'], 'keywords')
        )
        query_args_main.append({
            'multi_match': {
                'query': keywords,
                'operator': 'and',
                'type': 'cross_fields',
                'fields': [
                    'id^10',
                    'description',
                    'name^3',
                    'attribute_keywords',
                ],
            },
        })

    if 'source' in query_json:
        source = validate_str_list(query_json['source'], 'source')
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

    if 'types' in query_json:
        dataset_types = validate_str_list(query_json['types'], 'types')
        query_args_main.append({
            'bool': {
                'filter': [
                    {
                        'terms': {
                            'types': dataset_types,
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
        keywords = ' '.join(
            validate_str_list(query_json['keywords'], 'keywords')
        )
        if isinstance(keywords, list):
            keywords = ' '.join(keywords)
        query_sup_functions.append({
            'filter': {
                'multi_match': {
                    'query': keywords,
                    'operator': 'and',
                    'type': 'cross_fields',
                    'fields': [
                        'dataset_id^10',
                        'dataset_description',
                        'dataset_name^3',
                        'dataset_attribute_keywords',
                    ],
                },
            },
            'weight': 10,
        })

    if 'source' in query_json:
        source = validate_str_list(query_json['source'], 'source')
        query_sup_filters.append({
            'terms': {
                'dataset_source': source,
            }
        })

    if 'types' in query_json:
        dataset_types = validate_str_list(query_json['types'], 'types')
        if not isinstance(dataset_types, list):
            dataset_types = [dataset_types]
        query_sup_filters.append({
            'terms': {
                'dataset_types': dataset_types,
            }
        })

    return query_sup_functions, query_sup_filters


def parse_query_variables(data, geo_data=None):
    """Parses the variables of a Datamart query, turning it into an
    Elasticsearch query over 'datamart' index
    """

    output = list()
    tabular_variables = []

    if not data:
        return output, tabular_variables

    if not isinstance(data, list):
        raise ClientError("Invalid variables")

    for variable in data:
        if not isinstance(variable, dict):
            raise ClientError("Invalid variable")

        if 'type' not in variable:
            raise ClientError("variable is missing property 'type'")

        # temporal variable
        if variable['type'] == 'temporal_variable':
            filters = []
            if 'start' in variable or 'end' in variable:
                if 'start' in variable:
                    if not isinstance(variable['start'], str):
                        raise ClientError("Invalid variable start date")
                    start = parse_date(variable['start'])
                    if start is None:
                        raise ClientError("Invalid start date format")
                    start = start.timestamp()
                else:
                    start = 0

                if 'end' in variable:
                    if not isinstance(variable['end'], str):
                        raise ClientError("Invalid variable end date")
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
                        'path': 'temporal_coverage.ranges',
                        'query': {
                            'range': {
                                'temporal_coverage.ranges.range': {
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
                if (
                    not isinstance(granularity, str)
                    or granularity not in temporal_aggregation_keys
                ):
                    raise ClientError("Invalid temporal granularity")

                filters.append({
                    'term': {
                        'temporal_coverage.temporal_resolution': granularity,
                    },
                })

            output.append({
                'nested': {
                    'path': 'temporal_coverage',
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
            if 'area_name' in variable and geo_data:
                area_name = variable['area_name']
                if not isinstance(area_name, str):
                    raise ClientError("Invalid geospatial variable area")
                area = geo_data.resolve_name(area_name)
                if area is not None and area.bounds is not None:
                    bounds = area.bounds
                    longitude1, longitude2, latitude1, latitude2 = bounds
                    logger.info(
                        "Resolved area %r to %r",
                        area_name,
                        area,
                    )
                else:
                    logger.warning("Unknown area %r", area_name)
                    continue
            elif (
                'latitude1' in variable and
                'latitude2' in variable and
                'longitude1' in variable and
                'longitude2' in variable
            ):
                if not all(
                    isinstance(variable[i], float)
                    for i in (
                        'latitude1', 'latitude2', 'longitude1', 'longitude2'
                    )
                ):
                    raise ClientError("Invalid geospatial variable bounds")
                longitude1 = min(
                    float(variable['longitude1']),
                    float(variable['longitude2'])
                )
                longitude2 = max(
                    float(variable['longitude1']),
                    float(variable['longitude2'])
                )
                latitude1 = min(
                    float(variable['latitude1']),
                    float(variable['latitude2'])
                )
                latitude2 = max(
                    float(variable['latitude1']),
                    float(variable['latitude2'])
                )
            else:
                continue
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
                                                [longitude1, latitude2],
                                                [longitude2, latitude1],
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
                    if not isinstance(column_index, int):
                        raise ClientError("Invalid tabular variable index")
                    tabular_variables.append(column_index)

    return output, tabular_variables


def parse_query(query_json, geo_data=None):
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
            query_json['variables'],
            geo_data,
        )

    # TODO: for now, temporal and geospatial variables are ignored
    #   for 'datamart_columns', 'datamart_spatial_coverage', and
    #   'datamart_temporal_coverage' indices, since we do not have information
    #   about a dataset in these indices
    if variables_query:
        query_args_main.extend(variables_query)

    return query_args_main, query_sup_functions, query_sup_filters, list(set(tabular_variables))


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


class Search(BaseHandler, GracefulHandler, ProfilePostedData):
    @PROM_SEARCH.sync()
    def post(self):
        type_ = self.request.headers.get('Content-Type', '')
        data = None
        data_id = None
        data_profile = None
        if type_.startswith('application/json'):
            query = self.get_json()
        elif (type_.startswith('multipart/form-data') or
                type_.startswith('application/x-www-form-urlencoded')):
            # Get the query document
            query = self.get_body_argument('query', None)
            if query is None and 'query' in self.request.files:
                query = self.request.files['query'][0].body.decode('utf-8')
            if query is not None:
                query = json.loads(query)

            # Get the data
            data = self.get_body_argument('data', None)
            if 'data' in self.request.files:
                data = self.request.files['data'][0].body
            elif data is not None:
                data = data.encode('utf-8')

            # Get a reference to a dataset in the index
            data_id = self.get_body_argument('data_id', None)
            if 'data_id' in self.request.files:
                data_id = self.request.files['data_id'][0].body.decode('utf-8')

            # Get the data sketch JSON
            data_profile = self.get_body_argument('data_profile', None)
            if data_profile is None and 'data_profile' in self.request.files:
                data_profile = self.request.files['data_profile'][0].body
                data_profile = data_profile.decode('utf-8')
            if data_profile is not None:
                # Data profile can optionally be just the hash
                if len(data_profile) == 40 and profile_token_re.match(data_profile):
                    data_profile = self.application.redis.get(
                        'profile:' + data_profile,
                    )
                    if data_profile:
                        data_profile = json.loads(data_profile)
                    else:
                        return self.send_error_json(
                            404,
                            "Data profile token expired",
                        )
                else:
                    data_profile = json.loads(data_profile)

        elif (type_.startswith('text/csv') or
                type_.startswith('application/csv')):
            query = None
            data = self.request.body
        else:
            return self.send_error_json(
                400,
                "Either use multipart/form-data to send the 'query' JSON and "
                "'data' file (or 'data_profile' JSON), or use "
                "application/json to send a query alone, or use text/csv to "
                "send data alone",
            )

        if sum(1 for e in [data, data_id, data_profile] if e is not None) > 1:
            return self.send_error_json(
                400,
                "Please only provide one input dataset (either 'data', " +
                "'data_id', or  'data_profile')",
            )

        logger.info("Got search, content-type=%r%s%s%s%s",
                    type_.split(';')[0],
                    ', query' if query else '',
                    ', data' if data else '',
                    ', data_id' if data_id else '',
                    ', data_profile' if data_profile else '')

        # parameter: data
        if data is not None:
            data_profile, _ = self.handle_data_parameter(data)

        # parameter: data_id
        if data_id:
            data_profile = get_data_profile_from_es(
                self.application.elasticsearch,
                data_id,
            )
            if data_profile is None:
                return self.send_error_json(400, "No such dataset")

        # parameter: query
        query_args_main = list()
        query_sup_functions = list()
        query_sup_filters = list()
        tabular_variables = list()
        search_joins = search_unions = True
        if query:
            try:
                (
                    query_args_main,
                    query_sup_functions, query_sup_filters,
                    tabular_variables,
                ) = parse_query(query, self.application.geo_data)
            except ClientError as e:
                return self.send_error_json(400, str(e))
            if 'augmentation_type' in query:
                if query['augmentation_type'] == 'join':
                    search_unions = False
                elif query['augmentation_type'] == 'union':
                    search_joins = False
                else:
                    return self.send_error_json(
                        400,
                        "Unknown augmentation_type",
                    )

        # At least one of them must be provided
        if not query_args_main and not data_profile:
            return self.send_error_json(
                400,
                "At least one of 'data' or 'query' must be provided",
            )

        # Get pagination arguments
        page = self.get_query_argument('page', None)
        if page is not None:
            try:
                page = int(page)
            except ValueError:
                page = -1
            if page < 1:
                return self.send_error_json(400, "Invalid page number")
        size = self.get_query_argument('size', None)
        if size is not None:
            try:
                size = int(size)
            except ValueError:
                size = -1
            if size < 1 or size > 100:
                return self.send_error_json(400, "Invalid size")

        if not data_profile:
            page = page or 1
            size = size or TOP_K_SIZE
            response = self.application.elasticsearch.search(
                index='datamart',
                body={
                    'query': {
                        'bool': {
                            'must': query_args_main,
                        },
                    },
                    'aggs': {
                        'source': {
                            'terms': {
                                'field': 'source',
                            },
                        },
                        'license': {
                            'terms': {
                                'field': 'license',
                            },
                        },
                        'type': {
                            'terms': {
                                'field': 'types',
                            },
                        },
                    },
                },
                size=size,
                from_=(page - 1) * size,
            )
            hits = response['hits']['hits']

            total_pages = math.ceil(response['hits']['total']['value'] / size)
            self.set_header('X-Total-Pages', str(total_pages))

            results = []
            for h in hits:
                meta = h.pop('_source')
                results.append(dict(
                    id=h['_id'],
                    score=h['_score'],
                    metadata=meta,
                    augmentation={
                        'type': 'none',
                        'left_columns': [],
                        'left_columns_names': [],
                        'right_columns': [],
                        'right_columns_names': []
                    },
                    supplied_id=None,
                    supplied_resource_id=None
                ))

            aggs = {
                k: {
                    'buckets': {
                        b['key']: b['doc_count']
                        for b in v['buckets']
                    },
                    'incomplete': v['sum_other_doc_count'] != 0,
                }
                for k, v in response['aggregations'].items()
            }
            total = None
            if response['hits']['total']['relation'] == 'eq':
                total = response['hits']['total']['value']
        else:
            if page or size:
                return self.send_error_json(
                    400,
                    "Pagination is not yet supported for augmentation search",
                )
            results = get_augmentation_search_results(
                self.application.elasticsearch,
                self.application.lazo_client,
                data_profile,
                query_args_main,
                query_sup_functions,
                query_sup_filters,
                tabular_variables,
                ignore_datasets=[data_id] if data_id is not None else [],
                join=search_joins,
                union=search_unions,
            )
            aggs = None
            total = None

        results = [enhance_metadata(result) for result in results]

        # Private API for the frontend, don't want clients to rely on it
        if self.get_query_argument('_parse_sample', ''):
            for result in results:
                sample = result['metadata'].pop('sample', None)
                if sample:
                    result['sample'] = list(csv.reader(io.StringIO(sample)))

        response = {'results': results}
        if aggs is not None:
            response['facets'] = aggs
        if total is not None:
            response['total'] = total
        return self.send_json(response)
