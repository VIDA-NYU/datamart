import json
import logging
import numpy
import numpy.random
import prometheus_client
import re
import requests
from sklearn.cluster import KMeans
from sklearn.exceptions import ConvergenceWarning
from sklearn.neighbors._kd_tree import KDTree
import time
from urllib.parse import urlencode

from .warning_tools import ignore_warnings


logger = logging.getLogger(__name__)


N_RANGES = 3
MIN_RANGE_SIZE = 0.10  # 10%

SPATIAL_RANGE_DELTA_LONG = 0.0001
SPATIAL_RANGE_DELTA_LAT = 0.0001

MAX_ADDRESS_LENGTH = 90  # 90 characters
MAX_NOMINATIM_REQUESTS = 200
NOMINATIM_BATCH_SIZE = 30
NOMINATIM_MIN_SPLIT_BATCH_SIZE = 6  # Batches >=this are divided on failure

LATITUDE = ('latitude', 'lat', 'ycoord', 'y_coord')
LONGITUDE = ('longitude', 'long', 'lon', 'lng', 'xcoord', 'x_coord')


PROM_NOMINATIM_REQS = prometheus_client.Counter(
    'profile_nominatim_reqs', "Queries to Nominatim",
)
PROM_NOMINATIM_REQ_TIME = prometheus_client.Histogram(
    'profile_nominatim_req_seconds', "Time for Nominatim to answer a query",
)


def get_spatial_ranges(values):
    """
    Retrieve the spatial ranges (i.e. bounding boxes) given the input gps points.

    This performs K-Means clustering, returning a maximum of 3 ranges.
    """

    clustering = KMeans(n_clusters=min(N_RANGES, len(values)),
                        random_state=0)
    with ignore_warnings(ConvergenceWarning):
        clustering.fit(values)
    logger.info("K-Means clusters: %r", list(clustering.cluster_centers_))

    # Compute confidence intervals for each range
    ranges = []
    sizes = []
    for rg in range(N_RANGES):
        cluster = [values[i]
                   for i in range(len(values))
                   if clustering.labels_[i] == rg]
        if not cluster:
            continue

        # Eliminate clusters of outliers
        if len(cluster) < MIN_RANGE_SIZE * len(values):
            continue

        cluster.sort(key=lambda p: p[0])
        min_idx = int(0.05 * len(cluster))
        max_idx = int(0.95 * len(cluster))
        min_lat = cluster[min_idx][0]
        max_lat = cluster[max_idx][0]
        cluster.sort(key=lambda p: p[1])
        min_long = cluster[min_idx][1]
        max_long = cluster[max_idx][1]
        ranges.append([
            [min_long, max_lat],
            [max_long, min_lat],
        ])
        sizes.append(len(cluster))
    ranges.sort()
    logger.info("Ranges: %r", ranges)
    logger.info("Sizes: %r", sizes)

    # Lucene needs shapes to have an area for tessellation (no point or line)
    for rg in ranges:
        if rg[0][0] == rg[1][0]:
            rg[0][0] -= SPATIAL_RANGE_DELTA_LONG
            rg[1][0] += SPATIAL_RANGE_DELTA_LONG
        if rg[0][1] == rg[1][1]:
            rg[0][1] += SPATIAL_RANGE_DELTA_LAT
            rg[1][1] -= SPATIAL_RANGE_DELTA_LAT

    # Convert to Elasticsearch syntax
    ranges = [{'range': {'type': 'envelope',
                         'coordinates': coords}}
              for coords in ranges]
    return ranges


def normalize_latlong_column_name(name, substrings):
    name = name.strip().lower()
    for substr in substrings:
        idx = name.find(substr)
        if idx >= 0:
            name = name[:idx] + name[idx + len(substr):]
            break
    return name


def pair_latlong_columns(columns_lat, columns_long):
    # Normalize latitude column names
    normalized_lat = {}
    for i, (name, values_lat) in enumerate(columns_lat):
        name = normalize_latlong_column_name(name, LATITUDE)
        normalized_lat[name] = i

    # Go over normalized longitude column names and try to match
    pairs = []
    missed_long = []
    for name, values_long in columns_long:
        norm_name = normalize_latlong_column_name(name, LONGITUDE)
        if norm_name in normalized_lat:
            pairs.append((columns_lat[normalized_lat.pop(norm_name)],
                          (name, values_long)))
        else:
            missed_long.append(name)

    # Gather missed columns
    missed_lat = [columns_lat[i][0] for i in sorted(normalized_lat.values())]

    return pairs, (missed_lat, missed_long)


_re_loc = re.compile(
    r'\('
    r'(-?[0-9]{1,3}\.[0-9]{1,15})'
    r'(?:,| |(?:, ))'
    r'(-?[0-9]{1,3}\.[0-9]{1,15})'
    r'\)$'
)


def _parse_point(value):
    m = _re_loc.search(value)
    if m is not None:
        try:
            x = float(m.group(1))
            y = float(m.group(2))
        except ValueError:
            return None
        if -180.0 < x < 180.0 and -90.0 < y < 90.0:
            return y, x


def parse_wkt_column(values):
    """Parse a pandas.Series of points in WKT format into lat/long pairs.
    """
    # Parse points
    values = values.apply(_parse_point)
    # Drop NaN values
    values = values.dropna(axis=0)

    return list(values)


_nominatim_session = requests.Session()


def nominatim_query(url, *, q):
    if url[-1] == '/':
        url = url[:-1]
    res = start = end = None  # Avoids warnings
    for i in range(5):
        if i > 0:
            time.sleep(1)
        PROM_NOMINATIM_REQS.inc()  # Count all requests
        start = time.perf_counter()
        if isinstance(q, (tuple, list)):
            # Batch query
            res = _nominatim_session.get(
                url +
                '/search?' +
                urlencode({
                    'batch': json.dumps([{'q': qe} for qe in q]),
                    'format': 'jsonv2',
                }),
            )
        else:
            # Normal query
            res = _nominatim_session.get(
                url +
                '/search?' +
                urlencode({'q': q, 'format': 'jsonv2'}),
            )
        end = time.perf_counter()
        if res.status_code not in (502, 503, 504):
            break
    res.raise_for_status()
    # Record time for successful request
    PROM_NOMINATIM_REQ_TIME.observe(end - start)
    if not res.headers['Content-Type'].startswith('application/json'):
        raise requests.HTTPError(
            "Response is not JSON for URL: %s" % res.url,
            response=res,
        )
    if isinstance(q, (tuple, list)):
        return res.json()['batch']
    else:
        return res.json()


def _nominatim_batch(url, batch, locations, cache):
    try:
        locs = nominatim_query(url, q=list(batch.keys()))
    except requests.HTTPError as e:
        if (
            e.response.status_code == 500
            and len(batch) >= max(2, NOMINATIM_MIN_SPLIT_BATCH_SIZE)
        ):
            # Try smaller batch size
            batch_list = list(batch.items())
            mid = len(batch) // 2
            return (
                _nominatim_batch(url, dict(batch_list[:mid]), locations, cache)
                +
                _nominatim_batch(url, dict(batch_list[mid:]), locations, cache)
            )
        raise

    not_found = 0
    for location, (value, count) in zip(locs, batch.items()):
        if location:
            loc = (
                float(location[0]['lat']),
                float(location[0]['lon']),
            )
            cache[value] = loc
            locations.extend([loc] * count)
        else:
            cache[value] = None
            not_found += count
    batch.clear()
    return not_found


def nominatim_resolve_all(url, array, max_requests=MAX_NOMINATIM_REQUESTS):
    cache = {}
    locations = []
    not_found = 0  # Unique locations not found
    non_empty = 0
    start = time.perf_counter()
    processed = 0
    batch = {}

    for processed, value in enumerate(array):
        value = value.strip()
        if not value:
            continue
        non_empty += 1

        if len(value) > MAX_ADDRESS_LENGTH:
            continue
        elif value in cache:
            if cache[value] is not None:
                locations.append(cache[value])
        elif value in batch:
            batch[value] += 1
        else:
            batch[value] = 1
            if len(batch) == NOMINATIM_BATCH_SIZE:
                not_found += _nominatim_batch(url, batch, locations, cache)
                if len(cache) >= max_requests:
                    break

    if batch and len(cache) < max_requests:
        not_found += _nominatim_batch(url, batch, locations, cache)

    logger.info(
        "Performed %d Nominatim queries in %fs (%d hits). Found %d/%d",
        len(cache),
        time.perf_counter() - start,
        len(cache) - not_found,
        len(locations),
        processed,
    )
    return locations, non_empty


def median_smallest_distance(points, tree=None):
    """Median over all points of the distance to their closest neighbor.

    This gives an idea of the "grid size" of a point dataset.
    """
    points = numpy.array(points)
    if tree is None:
        # points = numpy.unique(points, axis=0)  # Too slow
        points = numpy.array(list(set(tuple(p) for p in points)))
        tree = KDTree(points)

    # Get the minimum distances to neighbors for a sample of points
    rnd = numpy.random.RandomState(89)
    sample_size = min(len(points), 100)
    sample_idx = rnd.choice(len(points), sample_size, replace=False)
    sample = points[sample_idx]
    distances, _ = tree.query(sample, k=2, return_distance=True)

    # Return the median of that
    return numpy.median(distances[:, 1])
