import contextlib
from datetime import datetime
import logging
import numpy
import os
import pandas
from pandas.errors import EmptyDataError
import prometheus_client
import random
import warnings

from .numerical import mean_stddev, get_numerical_ranges
from .profile_types import identify_types
from .spatial import nominatim_resolve_all, pair_latlong_columns, \
    get_spatial_ranges, parse_wkt_column
from .temporal import get_temporal_resolution
from . import types


logger = logging.getLogger(__name__)


RANDOM_SEED = 89

MAX_SIZE = 50000000  # 50 MB
SAMPLE_ROWS = 20

MAX_UNCLEAN_ADDRESSES = 0.20  # 20%


BUCKETS = [0.5, 1.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0, 300.0, 600.0]

PROM_PROFILE = prometheus_client.Histogram(
    'profile_seconds', "Profile time",
    buckets=BUCKETS,
)
PROM_TYPES = prometheus_client.Histogram(
    'profile_types_seconds', "Profile types time",
    buckets=BUCKETS,
)
PROM_SPATIAL = prometheus_client.Histogram(
    'profile_spatial_seconds', "Profile spatial coverage time",
    buckets=BUCKETS,
)


def truncate_string(s, limit=140):
    """Truncate a string, replacing characters over the limit with "...".
    """
    if len(s) <= limit:
        return s
    else:
        # Try to find a space
        space = s.rfind(' ', limit - 20, limit - 3)
        if space == -1:
            return s[:limit - 3] + "..."
        else:
            return s[:space] + "..."


@PROM_PROFILE.time()
def process_dataset(data, dataset_id=None, metadata=None,
                    lazo_client=None, nominatim=None,
                    search=False, include_sample=False,
                    coverage=True, plots=False, load_max_size=None, **kwargs):
    """Compute all metafeatures from a dataset.

    :param data: path to dataset, or file object, or DataFrame
    :param dataset_id: id of the dataset
    :param metadata: The metadata provided by the discovery plugin (might be
        very limited).
    :param lazo_client: client for the Lazo Index Server
    :param nominatim: URL of the Nominatim server
    :param search: True if this method is being called during the search
        operation (and not for indexing).
    :param include_sample: Set to True to include a few random rows to the
        result. Useful to present to a user.
    :param coverage: Whether to compute data ranges (using k-means)
    :param plots: Whether to compute plots
    :param load_max_size: Target size of the data to be analyzed. The data will
        be randomly sampled if it is bigger. Defaults to `MAX_SIZE`, currently
        50 MB. This is different from the sample data included in the result.
    :returns: JSON structure (dict)
    """
    if 'sample_size' in kwargs:
        warnings.warn(
            "Argument 'sample_size' is deprecated, use 'load_max_size'",
            DeprecationWarning,
        )
        load_max_size = kwargs.pop('sample_size')
    if kwargs:
        raise TypeError(
            "process_dataset() got unexpected keyword argument %r" %
            next(iter(kwargs))
        )

    if not load_max_size:
        load_max_size = MAX_SIZE

    if metadata is None:
        metadata = {}

    data_path = None
    if isinstance(data, pandas.DataFrame):
        metadata['nb_rows'] = len(data)
        # FIXME: no sampling here!
    else:
        with contextlib.ExitStack() as stack:
            if isinstance(data, (str, bytes)):
                if not os.path.exists(data):
                    raise ValueError("data file does not exist")

                # saving path
                data_path = data

                # File size
                metadata['size'] = os.path.getsize(data)
                logger.info("File size: %r bytes", metadata['size'])

                data = stack.enter_context(open(data, 'rb'))
            elif hasattr(data, 'read'):
                # Get size by seeking to the end
                data.seek(0, 2)
                metadata['size'] = data.tell()
                data.seek(0, 0)
            else:
                raise TypeError("data should be a filename, a file object, or "
                                "a pandas.DataFrame")

            # Load the data
            try:
                if metadata['size'] > load_max_size:
                    # Sub-sample
                    logger.info("Counting rows...")
                    metadata['nb_rows'] = sum(1 for _ in data)
                    data.seek(0, 0)

                    ratio = load_max_size / metadata['size']
                    logger.info("Loading dataframe, sample ratio=%r...", ratio)
                    rand = random.Random(RANDOM_SEED)
                    data = pandas.read_csv(
                        data,
                        dtype=str, na_filter=False,
                        skiprows=lambda i: i != 0 and rand.random() > ratio)
                else:
                    logger.info("Loading dataframe...")
                    data = pandas.read_csv(data,
                                           dtype=str, na_filter=False)

                    metadata['nb_rows'] = data.shape[0]
            except EmptyDataError:
                logger.warning("Dataframe is empty!")
                metadata['nb_rows'] = 0
                metadata['nb_profiled_rows'] = 0
                metadata['columns'] = []
                return metadata

            logger.info("Dataframe loaded, %d rows, %d columns",
                        data.shape[0], data.shape[1])

    metadata['nb_profiled_rows'] = data.shape[0]

    # Get column dictionary
    columns = metadata.setdefault('columns', [])
    # Fix size if wrong
    if len(columns) != len(data.columns):
        logger.info("Setting column names from header")
        columns[:] = [{} for _ in data.columns]
    else:
        logger.info("Keeping columns from discoverer")

    # Set column names
    for column_meta, name in zip(columns, data.columns):
        column_meta['name'] = name

    if data.shape[0] == 0:
        logger.info("0 rows, returning early")
        return metadata

    # Lat / Long
    columns_lat = []
    columns_long = []

    # Textual columns
    column_textual = []

    # Addresses
    resolved_addresses = {}

    # Identify types
    logger.info("Identifying types, %d columns...", len(columns))
    with PROM_TYPES.time():
        for i, column_meta in enumerate(columns):
            logger.info("Processing column %d...", i)
            array = data.iloc[:, i]
            # Identify types
            structural_type, semantic_types_dict, additional_meta = \
                identify_types(array, column_meta['name'])
            # Set structural type
            column_meta['structural_type'] = structural_type
            # Add semantic types to the ones already present
            sem_types = column_meta.setdefault('semantic_types', [])
            for sem_type in semantic_types_dict:
                if sem_type not in sem_types:
                    sem_types.append(sem_type)
            # Insert additional metadata
            column_meta.update(additional_meta)

            # Compute ranges for numerical data
            if structural_type in (types.INTEGER, types.FLOAT):
                # Get numerical ranges
                numerical_values = []
                for e in array:
                    try:
                        e = float(e)
                    except ValueError:
                        e = None
                    else:
                        if not (-3.4e38 < e < 3.4e38):  # Overflows in ES
                            e = None
                    numerical_values.append(e)

                column_meta['mean'], column_meta['stddev'] = \
                    mean_stddev(numerical_values)

                # Compute histogram from numerical values
                if plots:
                    counts, edges = numpy.histogram(
                        [v for v in numerical_values if v is not None],
                        bins=10,
                    )
                    counts = [int(i) for i in counts]
                    edges = [float(f) for f in edges]
                    column_meta['plot'] = {
                        "type": "histogram_numerical",
                        "data": [
                            {
                                "count": count,
                                "bin_start": edges[i],
                                "bin_end": edges[i + 1],
                            }
                            for i, count in enumerate(counts)
                        ]
                    }

                # Get lat/long columns
                if types.LATITUDE in semantic_types_dict:
                    columns_lat.append(
                        (column_meta['name'], numerical_values)
                    )
                elif types.LONGITUDE in semantic_types_dict:
                    columns_long.append(
                        (column_meta['name'], numerical_values)
                    )
                elif coverage:
                    ranges = get_numerical_ranges(
                        [x for x in numerical_values if x is not None]
                    )
                    if ranges:
                        column_meta['coverage'] = ranges

            # Compute ranges for temporal data
            if (coverage or plots) and types.DATE_TIME in semantic_types_dict:
                timestamps = numpy.empty(
                    len(semantic_types_dict[types.DATE_TIME]),
                    dtype='float32',
                )
                for j, dt in enumerate(
                        semantic_types_dict[types.DATE_TIME]):
                    timestamps[j] = dt.timestamp()
                column_meta['mean'], column_meta['stddev'] = \
                    mean_stddev(timestamps)

                # Get temporal ranges
                if coverage:
                    ranges = get_numerical_ranges(timestamps)
                    if ranges:
                        column_meta['coverage'] = ranges

                # Get temporal resolution
                column_meta['temporal_resolution'] = get_temporal_resolution(
                    semantic_types_dict[types.DATE_TIME],
                )

                # Compute histogram from temporal values
                if plots:
                    counts, edges = numpy.histogram(timestamps, bins=10)
                    counts = [int(i) for i in counts]
                    column_meta['plot'] = {
                        "type": "histogram_temporal",
                        "data": [
                            {
                                "count": count,
                                "date_start": datetime.utcfromtimestamp(
                                    edges[i],
                                ).isoformat(),
                                "date_end": datetime.utcfromtimestamp(
                                    edges[i + 1],
                                ).isoformat(),
                            }
                            for i, count in enumerate(counts)
                        ]
                    }

            # Compute histogram from categorical values
            if plots and types.CATEGORICAL in semantic_types_dict:
                counts = {}
                for value in array:
                    if not value:
                        continue
                    try:
                        counts[value] += 1
                    except KeyError:
                        counts[value] = 1
                counts = sorted(
                    counts.items(),
                    key=lambda p: p[1],
                )[:5]
                counts = sorted(counts)
                column_meta['plot'] = {
                    "type": "histogram_categorical",
                    "data": [
                        {
                            "bin": value,
                            "count": count,
                        }
                        for value, count in counts
                    ]
                }

            if (
                structural_type == types.TEXT and
                types.DATE_TIME not in semantic_types_dict
            ):
                column_textual.append(column_meta['name'])

            # Resolve addresses into coordinates
            if (
                nominatim is not None and
                structural_type == types.TEXT and
                types.TEXT in semantic_types_dict
            ):
                locations, non_empty = nominatim_resolve_all(
                    nominatim,
                    array,
                )
                if non_empty > 0:
                    unclean_ratio = 1.0 - len(locations) / non_empty
                    if unclean_ratio <= MAX_UNCLEAN_ADDRESSES:
                        resolved_addresses[column_meta['name']] = locations
                        if types.ADDRESS not in column_meta['semantic_types']:
                            column_meta['semantic_types'].append(types.ADDRESS)

    # Textual columns
    if lazo_client and column_textual:
        # Indexing with lazo
        if not search:
            # TODO: Remove previous data from lazo
            logger.info("Indexing textual data with Lazo...")
            try:
                if data_path:
                    # if we have the path, send the path
                    lazo_client.index_data_path(
                        data_path,
                        dataset_id,
                        column_textual
                    )
                else:
                    # if path is not available, send the data instead
                    for column_name in column_textual:
                        lazo_client.index_data(
                            data[column_name].values.tolist(),
                            dataset_id,
                            column_name
                        )
            except Exception:
                logger.error("Error indexing textual attributes from %s", dataset_id)
                raise
        # Generating Lazo sketches for the search
        else:
            logger.info("Generating Lazo sketches...")
            try:
                if data_path:
                    # if we have the path, send the path
                    lazo_sketches = lazo_client.get_lazo_sketch_from_data_path(
                        data_path,
                        "",
                        column_textual,
                    )
                else:
                    # if path is not available, send the data instead
                    lazo_sketches = []
                    for column_name in column_textual:
                        lazo_sketches.append(
                            lazo_client.get_lazo_sketch_from_data(
                                data[column_name].values.tolist(),
                                "",
                                column_name,
                            )
                        )
                # saving sketches into metadata
                for i in range(len(column_textual)):
                    n_permutations, hash_values, cardinality = \
                        lazo_sketches[i]
                    columns[i]['lazo'] = dict(
                        n_permutations=n_permutations,
                        hash_values=list(hash_values),
                        cardinality=cardinality,
                    )
            except Exception:
                logger.warning("Error getting Lazo sketches")
                raise

    # Lat / Long
    if coverage:
        logger.info("Computing spatial coverage...")
        spatial_coverage = []
        with PROM_SPATIAL.time():
            # Pair lat & long columns
            pairs, (missed_lat, missed_long) = \
                pair_latlong_columns(columns_lat, columns_long)

            # Log missed columns
            if missed_lat:
                logger.warning("Unmatched latitude columns: %r", missed_lat)
            if missed_long:
                logger.warning("Unmatched longitude columns: %r", missed_long)

            # Remove semantic type from unpaired columns
            for col in columns:
                if col['name'] in missed_lat:
                    col['semantic_types'].remove(types.LATITUDE)
                if col['name'] in missed_long:
                    col['semantic_types'].remove(types.LONGITUDE)

            # Compute ranges from lat/long pairs
            for (name_lat, values_lat), (name_long, values_long) in pairs:
                values = []
                for lat, long in zip(values_lat, values_long):
                    if (lat and long and  # Ignore None and 0
                            -90 < lat < 90 and -180 < long < 180):
                        values.append((lat, long))

                if len(values) > 1:
                    logger.info(
                        "Computing spatial ranges lat=%r long=%r (%d rows)",
                        name_lat, name_long, len(values),
                    )
                    spatial_ranges = get_spatial_ranges(values)
                    if spatial_ranges:
                        spatial_coverage.append({"lat": name_lat,
                                                 "lon": name_long,
                                                 "ranges": spatial_ranges})

            # Compute ranges from WKT points
            geo_point_columns = [
                i for i, col in enumerate(columns)
                if col['structural_type'] == types.GEO_POINT
            ]
            for i in geo_point_columns:
                name = data.columns[i]
                values = parse_wkt_column(data.iloc[:, i])
                logger.info(
                    "Computing spatial ranges point=%r (%d rows)",
                    name, len(values),
                )
                spatial_ranges = get_spatial_ranges(values)
                if spatial_ranges:
                    spatial_coverage.append({"point": name,
                                             "ranges": spatial_ranges})

            # Compute ranges from addresses
            for name, values in resolved_addresses.items():
                logger.info(
                    "Computing spatial ranges address=%r (%d rows)",
                    name, len(values),
                )
                spatial_ranges = get_spatial_ranges(values)
                if spatial_ranges:
                    spatial_coverage.append({"address": name,
                                             "ranges": spatial_ranges})

        if spatial_coverage:
            metadata['spatial_coverage'] = spatial_coverage

    # Sample data
    if include_sample:
        rand = numpy.random.RandomState(RANDOM_SEED)
        choose_rows = rand.choice(
            len(data),
            min(SAMPLE_ROWS, len(data)),
            replace=False,
        )
        choose_rows.sort()  # Keep it in order
        sample = data.iloc[choose_rows]
        sample = sample.applymap(truncate_string)  # Truncate long values
        metadata['sample'] = sample.to_csv(index=False)

    # Return it -- it will be inserted into Elasticsearch, and published to the
    # feed and the waiting on-demand searches
    return metadata
