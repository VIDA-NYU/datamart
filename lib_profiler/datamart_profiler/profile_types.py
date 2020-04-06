from datetime import datetime
import dateutil.parser
import dateutil.tz
import re

from . import types
from .warning_tools import raise_warnings


_re_int = re.compile(r'^[+-]?[0-9]+'
                     r'(?:\.0*)?'  # 4.0 and 7.000 are integers
                     r'$')
_re_float = re.compile(r'^[+-]?'
                       r'(?:'
                       r'(?:[0-9]+\.[0-9]*)|'
                       r'(?:\.[0-9]+)'
                       r')'
                       r'(?:[Ee][0-9]+)?$')
_re_whitespace = re.compile(r'\s')


# Tolerable ratio of unclean data
MAX_UNCLEAN = 0.02  # 2%


# Maximum number of different values for categorical columns
MAX_CATEGORICAL_RATIO = 0.10  # 10%


_defaults = datetime(1985, 1, 1), datetime(2005, 6, 15)


def parse_date(string):
    with raise_warnings(dateutil.parser.UnknownTimezoneWarning):
        # This is a dirty trick because dateutil returns a datetime for strings
        # than only contain times. We parse it twice with different defaults,
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


def identify_types(array, name):
    num_total = len(array)
    ratio = 1.0 - MAX_UNCLEAN

    # Identify structural type
    num_float = num_int = num_bool = num_empty = num_text = 0
    for elem in array:
        if not elem:
            num_empty += 1
        elif _re_int.match(elem):
            num_int += 1
        elif _re_float.match(elem):
            num_float += 1
        elif len(_re_whitespace.findall(elem)) >= 4:
            num_text += 1
        if elem.lower() in ('0', '1', 'true', 'false', 'y', 'n', 'yes', 'no'):
            num_bool += 1

    threshold = ratio * (num_total - num_empty)

    if num_empty == num_total:
        structural_type = types.MISSING_DATA
    elif num_int >= threshold:
        structural_type = types.INTEGER
    elif num_int + num_float >= threshold:
        structural_type = types.FLOAT
    else:
        structural_type = types.TEXT

    semantic_types_dict = {}
    column_meta = {}

    if structural_type != types.MISSING_DATA and num_empty > 0:
        column_meta['missing_values_ratio'] = num_empty / num_total

    # Identify booleans
    if structural_type != types.MISSING_DATA and num_bool >= threshold:
        semantic_types_dict[types.BOOLEAN] = None
        column_meta['unclean_values_ratio'] = \
            (num_total - num_empty - num_bool) / num_total

    if structural_type == types.TEXT:
        if num_text >= threshold:
            # Free text
            semantic_types_dict[types.TEXT] = None
        else:
            # Count distinct values
            values = set(e for e in array if e)
            column_meta['num_distinct_values'] = len(values)
            max_categorical = MAX_CATEGORICAL_RATIO * (len(array) - num_empty)
            if (
                len(values) <= max_categorical or
                types.BOOLEAN in semantic_types_dict
            ):
                semantic_types_dict[types.CATEGORICAL] = values
    elif structural_type == types.INTEGER:
        # Identify ids
        # TODO: is this enough?
        # TODO: what about false positives?
        if (name.lower().startswith('id') or
                name.lower().endswith('id') or
                name.lower().startswith('identifier') or
                name.lower().endswith('identifier') or
                name.lower().startswith('index') or
                name.lower().endswith('index')):
            semantic_types_dict[types.ID] = None

    # Identify lat/long
    if structural_type == types.FLOAT:
        num_lat = num_long = 0
        for elem in array:
            try:
                elem = float(elem)
            except ValueError:
                pass
            else:
                if -180.0 <= float(elem) <= 180.0:
                    num_long += 1
                    if -90.0 <= float(elem) <= 90.0:
                        num_lat += 1

        if num_lat >= threshold and 'lat' in name.lower():
            semantic_types_dict[types.LATITUDE] = None
        if num_long >= threshold and 'lon' in name.lower():
            semantic_types_dict[types.LONGITUDE] = None

    # Identify dates
    if structural_type == types.TEXT:
        parsed_dates = []
        for elem in array:
            elem = parse_date(elem)
            if elem is not None:
                parsed_dates.append(elem)

        if len(parsed_dates) >= threshold:
            semantic_types_dict[types.DATE_TIME] = parsed_dates

    return structural_type, semantic_types_dict, column_meta
