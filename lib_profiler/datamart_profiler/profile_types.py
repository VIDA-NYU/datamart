from datetime import datetime
import dateutil.tz
import re
import regex

from . import types
from .temporal import parse_date


_re_int = re.compile(
    r'^[+-]?[0-9]+'
    r'(?:\.0*)?'  # 4.0 and 7.000 are integers
    r'$'
)
_re_float = re.compile(
    r'^[+-]?'
    r'(?:'
    r'(?:[0-9]+\.[0-9]*)|'
    r'(?:\.[0-9]+)'
    r')'
    r'(?:[Ee][0-9]+)?$'
)
_re_wkt_point = re.compile(
    r'^POINT ?\('
    r'-?[0-9]{1,3}\.[0-9]{1,15}'
    r' '
    r'-?[0-9]{1,3}\.[0-9]{1,15}'
    r'\)$'
)
_re_wkt_polygon = re.compile(
    r'^POLYGON ?\('
    r'('
    r'\([0-9 .]+\)'
    r', ?)*'
    r'\([0-9 .]+\)'
    r'\)$'
)
_re_geo_combined = regex.compile(
    r'^([\p{Lu}\p{Po}0-9 ])+ \('
    r'-?[0-9]{1,3}\.[0-9]{1,15}'
    r', ?'
    r'-?[0-9]{1,3}\.[0-9]{1,15}'
    r'\)$'
)
_re_whitespace = re.compile(r'\s')


# Tolerable ratio of unclean data
MAX_UNCLEAN = 0.02  # 2%


# Maximum number of different values for categorical columns
MAX_CATEGORICAL_RATIO = 0.10  # 10%


def identify_types(array, name, geo_data):
    num_total = len(array)

    column_meta = {}

    # Identify structural type
    num_float = num_int = num_bool = num_empty = 0
    num_point = num_geo_combined = num_polygon = num_text = 0
    for elem in array:
        if not elem:
            num_empty += 1
        elif _re_int.match(elem):
            num_int += 1
        elif _re_float.match(elem):
            num_float += 1
        elif _re_wkt_point.match(elem):
            num_point += 1
        elif _re_geo_combined.match(elem):
            num_geo_combined += 1
        elif _re_wkt_polygon.match(elem):
            num_polygon += 1
        elif len(_re_whitespace.findall(elem)) >= 4:
            num_text += 1
        if elem.lower() in ('0', '1', 'true', 'false', 'y', 'n', 'yes', 'no'):
            num_bool += 1

    threshold = max(1, (1.0 - MAX_UNCLEAN) * (num_total - num_empty))

    if num_empty == num_total:
        structural_type = types.MISSING_DATA
    elif num_int >= threshold:
        structural_type = types.INTEGER
        column_meta['unclean_values_ratio'] = \
            (num_total - num_empty - num_int) / num_total
    elif num_int + num_float >= threshold:
        structural_type = types.FLOAT
        column_meta['unclean_values_ratio'] = \
            (num_total - num_empty - num_int - num_float) / num_total
    elif num_point >= threshold or num_geo_combined >= threshold:
        structural_type = types.GEO_POINT
        column_meta['unclean_values_ratio'] = \
            (num_total - num_empty - num_point) / num_total
    elif num_polygon >= threshold:
        structural_type = types.GEO_POLYGON
        column_meta['unclean_values_ratio'] = \
            (num_total - num_empty - num_polygon) / num_total
    else:
        structural_type = types.TEXT

    # TODO: structural or semantic types?

    semantic_types_dict = {}

    if structural_type != types.MISSING_DATA and num_empty > 0:
        column_meta['missing_values_ratio'] = num_empty / num_total

    # Identify booleans
    if num_bool >= threshold:
        semantic_types_dict[types.BOOLEAN] = None
        column_meta['unclean_values_ratio'] = \
            (num_total - num_empty - num_bool) / num_total

    if structural_type == types.TEXT:
        categorical = False

        if geo_data is not None:
            resolved = geo_data.resolve_names(array)
            if sum(1 for r in resolved if r is not None) > 0.7 * len(array):
                semantic_types_dict[types.ADMIN] = resolved
                categorical = True

        if not categorical and num_text >= threshold:
            # Free text
            semantic_types_dict[types.TEXT] = None
        else:
            # Count distinct values
            values = set(e for e in array if e)
            column_meta['num_distinct_values'] = len(values)
            max_categorical = MAX_CATEGORICAL_RATIO * (len(array) - num_empty)
            if (
                categorical or
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

        # Count distinct values
        values = set(e for e in array if e)
        column_meta['num_distinct_values'] = len(values)

        # Identify years
        if name.strip().lower() == 'year':
            dates = []
            for year in array:
                try:
                    dates.append(datetime(
                        int(year), 1, 1,
                        tzinfo=dateutil.tz.UTC,
                    ))
                except ValueError:
                    pass
            if len(dates) >= threshold:
                semantic_types_dict[types.DATE_TIME] = dates

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
    parsed_dates = []
    for elem in array:
        elem = parse_date(elem)
        if elem is not None:
            parsed_dates.append(elem)

    if len(parsed_dates) >= threshold:
        semantic_types_dict[types.DATE_TIME] = parsed_dates
        if structural_type == types.INTEGER:
            # 'YYYYMMDD' format means values can be parsed as integers, but
            # that's not what they are
            structural_type = types.TEXT

    return structural_type, semantic_types_dict, column_meta
