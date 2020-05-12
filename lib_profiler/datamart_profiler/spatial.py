import json
import logging
import numpy
import prometheus_client
import requests
from shapely import wkt
from sklearn.cluster import KMeans
from sklearn.exceptions import ConvergenceWarning
import time
from urllib.parse import urlencode

from .warning_tools import ignore_warnings


logger = logging.getLogger(__name__)


N_RANGES = 3

SPATIAL_RANGE_DELTA_LONG = 0.0001
SPATIAL_RANGE_DELTA_LAT = 0.0001

MAX_ADDRESS_LENGTH = 90  # 90 characters
MAX_NOMINATIM_REQUESTS = 200
NOMINATIM_BATCH_SIZE = 30

LATITUDE = ('latitude', 'lat')
LONGITUDE = ('longitude', 'long', 'lon', 'lng')


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

    # TODO: Deal with clusters made of outliers

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


def _wkt_loader(value):
    try:
        point = wkt.loads(value)
    except Exception:
        pass
    else:
        if -180.0 < point.x < 180.0 and -90.0 < point.y < 90.0:
            return point
    return numpy.nan


def parse_wkt_column(values):
    """Parse a pandas.Series of points in WKT format into lat/long pairs.
    """
    # Parse points using shapely
    # Use a wrapper around shapely.wkt.loads to ignore errors
    values = values.apply(_wkt_loader)
    # Drop NaN values
    values = values.dropna(axis=0)
    # Turn the points into (lat, long) pairs
    values = list((p.y, p.x) for p in values)

    return values


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


def nominatim_resolve_all(url, array, max_requests=MAX_NOMINATIM_REQUESTS):
    cache = {}
    locations = []
    not_found = 0  # Unique locations not found
    non_empty = 0
    start = time.perf_counter()
    processed = 0
    batch = {}

    def run_batch():
        not_found_batch = 0
        locs = nominatim_query(url, q=list(batch.keys()))
        for location, count in zip(locs, batch.values()):
            if location:
                loc = (
                    float(location[0]['lat']),
                    float(location[0]['lon']),
                )
                cache[value] = loc
                locations.extend([loc] * count)
            else:
                cache[value] = None
                not_found_batch += count
        batch.clear()
        return not_found_batch

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
                not_found += run_batch()
                if len(cache) >= max_requests:
                    break

    if batch and len(cache) < max_requests:
        not_found += run_batch()

    logger.info(
        "Performed %d Nominatim queries in %fs (%d hits). Found %d/%d",
        len(cache),
        time.perf_counter() - start,
        len(cache) - not_found,
        len(locations),
        processed,
    )
    return locations, non_empty
