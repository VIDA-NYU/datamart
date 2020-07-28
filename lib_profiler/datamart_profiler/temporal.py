import collections
from datetime import datetime
import dateutil.parser
import dateutil.tz
import logging
import pandas

from .warning_tools import raise_warnings


logger = logging.getLogger(__name__)


# Keep in sync with frontend's TemporalResolution
temporal_aggregation_keys = {
    'year': '%Y',
    'quarter': lambda dt: pandas.Timestamp(
        year=dt.year,
        month=((dt.month - 1) // 3) * 3 + 1,
        day=1,
    ),
    'month': '%Y-%m',
    'week': lambda dt: (
        # Simply using "%Y-%W" doesn't work at year boundaries
        # Map each timestamp to the first day of its week
        (dt - pandas.Timedelta(days=dt.weekday())).strftime('%Y-%m-%d')
    ),
    'day': '%Y-%m-%d',
    'hour': '%Y-%m-%d %H',
    'minute': '%Y-%m-%d %H:%M',
    'second': '%Y-%m-%d %H:%M:%S',
}


def get_temporal_resolution(values):
    """Returns the resolution of the temporal attribute.
    """

    # Python 3.7+ iterates on dict in insertion order
    for resolution, key in temporal_aggregation_keys.items():
        counts = collections.defaultdict(collections.Counter)
        if isinstance(key, str):
            for value in values:
                bin = value.strftime(key)
                counts[bin][value] += 1
        else:
            for value in values:
                bin = key(value)
                counts[bin][value] += 1

        avg_per_bin = sum(len(v) for v in counts.values()) / len(counts)
        if avg_per_bin < 1.05:
            # 5 % error tolerated
            return resolution

    return 'second'


_defaults = datetime(1985, 1, 1), datetime(2005, 6, 1)


def parse_date(string):
    with raise_warnings(dateutil.parser.UnknownTimezoneWarning):
        # This is a dirty trick because dateutil returns a datetime for strings
        # that only contain times. We parse it twice with different defaults,
        # so we can tell whether the default date is used in the result
        try:
            dt1 = dateutil.parser.parse(string, default=_defaults[0])
            dt2 = dateutil.parser.parse(string, default=_defaults[1])
        except Exception:  # ValueError, OverflowError, UnknownTimezoneWarning
            return None

    if dt1 != dt2:
        # It was not a date, just a time; no good
        return None

    # If no timezone was read, assume UTC
    if dt1.tzinfo is None:
        dt1 = dt1.replace(tzinfo=dateutil.tz.UTC)
    return dt1
