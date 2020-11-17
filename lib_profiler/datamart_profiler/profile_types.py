import collections
from datetime import datetime
import dateutil.tz
import functools
import re
import regex

from . import types
from .spatial import LATITUDE, LONGITUDE, disambiguate_admin_areas
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
_re_other_point = re.compile(
    r'^POINT ?\('
    r'-?[0-9]{1,3}\.[0-9]{1,15}'
    r', ?'
    r'-?[0-9]{1,3}\.[0-9]{1,15}'
    r'\)$'
)
_re_latlong_point = re.compile(
    r'^\('
    r'-?[0-9]{1,3}\.[0-9]{1,15}'
    r', ?'
    r'-?[0-9]{1,3}\.[0-9]{1,15}'
    r'\)$'
)
_re_whitespace = re.compile(r'\s+')


# Tolerable ratio of unclean data
MAX_UNCLEAN = 0.02  # 2%


# Free text is multiple words
# At least 50% of it needs to have at least 4 words
TEXT_WORDS = 4
TEXT_WORDS_THRESHOLD = 0.5  # 50%


# Maximum number of different values for categorical columns
MAX_CATEGORICAL_RATIO = 0.10  # 10%


def regular_exp_count(array):
    """Count instances matching the structure of each data type, using regexes.
    """
    re_count = collections.Counter()

    for elem in array:
        if not elem:
            re_count['empty'] += 1
        elif _re_int.match(elem):
            re_count['int'] += 1
        elif _re_float.match(elem):
            re_count['float'] += 1
        elif _re_wkt_point.match(elem):
            re_count['point'] += 1
        elif _re_geo_combined.match(elem):
            re_count['geo_combined'] += 1
        elif _re_other_point.match(elem):
            re_count['other_point'] += 1
        elif _re_latlong_point.match(elem):
            re_count['latlong_point'] += 1
        elif _re_wkt_polygon.match(elem):
            re_count['polygon'] += 1
        elif len(_re_whitespace.findall(elem)) >= TEXT_WORDS - 1:
            re_count['text'] += 1
        if elem.lower() in ('0', '1', 'true', 'false', 'y', 'n', 'yes', 'no'):
            re_count['bool'] += 1

    return re_count


def unclean_values_ratio(c_type, re_count, num_total):
    """Count how many values don't match a given type.

    This takes into account that a valid int is also a valid float, etc.
    """
    ratio = 0
    if c_type == types.INTEGER:
        ratio = \
            (num_total - re_count['empty'] - re_count['int']) / num_total
    if c_type == types.FLOAT:
        ratio = \
            (num_total - re_count['empty'] - re_count['int'] - re_count['float']) / num_total
    if c_type == types.GEO_POINT:
        ratio = \
            (num_total - re_count['empty'] - re_count['point'] - re_count['geo_combined'] - re_count['other_point']) / num_total
    if c_type == types.GEO_POINT_LATLONG:
        ratio = \
            (num_total - re_count['empty'] - re_count['latlong_point']) / num_total
    if c_type == types.GEO_POLYGON:
        ratio = \
            (num_total - re_count['empty'] - re_count['polygon']) / num_total
    if c_type == types.BOOLEAN:
        ratio = \
            (num_total - re_count['empty'] - re_count['bool']) / num_total
    return ratio


def parse_dates(array):
    """Parse the valid dates in an array of strings.
    """
    parsed_dates = []
    for elem in array:
        elem = parse_date(elem)
        if elem is not None:
            parsed_dates.append(elem)
    return parsed_dates


def identify_structural_type(re_count, num_total, threshold):
    """Picks the structural type from counts computed by `regular_exp_count()`.
    """
    if re_count['empty'] == num_total:
        structural_type = types.MISSING_DATA
    elif re_count['int'] >= threshold:
        structural_type = types.INTEGER
    elif re_count['int'] + re_count['float'] >= threshold:
        structural_type = types.FLOAT
    elif re_count['point'] >= threshold or re_count['geo_combined'] >= threshold or re_count['other_point'] >= threshold:
        structural_type = types.GEO_POINT
    elif re_count['latlong_point'] >= threshold:
        structural_type = types.GEO_POINT_LATLONG
    elif re_count['polygon'] >= threshold:
        structural_type = types.GEO_POLYGON
    else:
        structural_type = types.TEXT

    return structural_type


def identify_types(array, name, geo_data, manual=None):
    """Identify the structural type and semantic types of an array.

    :param array: The list, series, or array to inspect
    :param name: The name of this column. This is taken into account for some
        heuristics like latitude, longitude, year number.
    :param manual: Manual information provided by the user that will be
        reconciled with the observed data.
    :return: A tuple ``(structural_type, semantic_types_dict, column_meta)``
        where `structural_type` is the detected structural type (e.g. storage
        format), `semantic_types_dict` is a dict mapping semantic types (e.g.
        meaning) to parsed values for further processing, and `column_meta`
        contains additional information about the column (not related to type).
    """
    num_total = len(array)
    column_meta = {}

    # This function let you check/count how many instances match a structure of particular data type
    re_count = regular_exp_count(array)

    # Identify structural type and compute unclean values ratio
    threshold = max(1, (1.0 - MAX_UNCLEAN) * (num_total - re_count['empty']))
    if manual:
        structural_type = manual['structural_type']
        column_meta['unclean_values_ratio'] = unclean_values_ratio(structural_type, re_count, num_total)
    else:
        structural_type = identify_structural_type(re_count, num_total, threshold)
        if structural_type != types.MISSING_DATA and structural_type != types.TEXT:
            column_meta['unclean_values_ratio'] = unclean_values_ratio(structural_type, re_count, num_total)

    # compute missing values ratio
    if structural_type != types.MISSING_DATA and re_count['empty'] > 0:
        column_meta['missing_values_ratio'] = re_count['empty'] / num_total

    distinct_values = functools.lru_cache()(lambda: set(e for e in array if e))

    semantic_types_dict = {}
    if manual:
        semantic_types = manual['semantic_types']
        semantic_types_dict = {el: None for el in semantic_types}

        for el in semantic_types:
            if el == types.BOOLEAN:
                column_meta['unclean_values_ratio'] = \
                    unclean_values_ratio(types.BOOLEAN, re_count, num_total)
            if el == types.DATE_TIME:
                dates = parse_dates(array)
                semantic_types_dict[types.DATE_TIME] = dates
            if el == types.ADMIN:
                if geo_data is not None and len(distinct_values()) >= 3:
                    admin_areas = geo_data.resolve_names_all(array)
                    admin_areas = [r for r in admin_areas if r]
                    if admin_areas:
                        admin_areas = disambiguate_admin_areas(admin_areas)
                        if admin_areas is not None:
                            semantic_types_dict[types.ADMIN] = admin_areas
            if el == types.CATEGORICAL or el == types.INTEGER:
                # Count distinct values
                column_meta['num_distinct_values'] = len(distinct_values())
                if el == types.CATEGORICAL:
                    semantic_types_dict[types.CATEGORICAL] = distinct_values()
    else:
        # Identify booleans
        num_bool = re_count['bool']
        num_text = re_count['text']
        num_empty = re_count['empty']

        if num_bool >= threshold:
            semantic_types_dict[types.BOOLEAN] = None
            column_meta['unclean_values_ratio'] = \
                unclean_values_ratio(types.BOOLEAN, re_count, num_total)

        if structural_type == types.TEXT:
            categorical = False

            # Administrative areas
            if geo_data is not None and len(distinct_values()) >= 3:
                admin_areas = geo_data.resolve_names_all(distinct_values())
                admin_areas = [r for r in admin_areas if r]
                if len(admin_areas) > 0.7 * len(distinct_values()):

                    admin_areas = disambiguate_admin_areas(admin_areas)
                    if admin_areas is not None:
                        semantic_types_dict[types.ADMIN] = admin_areas
                        categorical = True

            # Different threshold there, we don't need all text to be many words
            text_threshold = max(
                1,
                (1.0 - TEXT_WORDS_THRESHOLD) * (num_total - re_count['empty']),
            )
            if not categorical and num_text >= text_threshold:
                # Free text
                semantic_types_dict[types.TEXT] = None
            else:
                # Count distinct values
                column_meta['num_distinct_values'] = len(distinct_values())
                max_categorical = MAX_CATEGORICAL_RATIO * (len(array) - num_empty)
                if (
                    categorical or
                    len(distinct_values()) <= max_categorical or
                    types.BOOLEAN in semantic_types_dict
                ):
                    semantic_types_dict[types.CATEGORICAL] = distinct_values()
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
            column_meta['num_distinct_values'] = len(distinct_values())

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
                    structural_type = types.TEXT
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

            if num_lat >= threshold and any(n in name.lower() for n in LATITUDE):
                semantic_types_dict[types.LATITUDE] = None
            if num_long >= threshold and any(n in name.lower() for n in LONGITUDE):
                semantic_types_dict[types.LONGITUDE] = None

        # Identify dates
        parsed_dates = parse_dates(array)

        if len(parsed_dates) >= threshold:
            semantic_types_dict[types.DATE_TIME] = parsed_dates
            if structural_type == types.INTEGER:
                # 'YYYYMMDD' format means values can be parsed as integers, but
                # that's not what they are
                structural_type = types.TEXT

    return structural_type, semantic_types_dict, column_meta


SPATIAL_STRUCTURAL_TYPES = {
    types.GEO_POINT, types.GEO_POINT_LATLONG, types.GEO_POLYGON,
}
SPATIAL_SEMANTIC_TYPES = {
    types.LATITUDE, types.LONGITUDE,
    types.ADDRESS,
    types.ADMIN,
}


def determine_dataset_type(column_structural_type, column_semantic_types):
    """Determines dataset types from columns' structural and semantic types.
    """
    if column_structural_type in SPATIAL_STRUCTURAL_TYPES:
        return types.DATASET_SPATIAL
    elif any(t in SPATIAL_SEMANTIC_TYPES for t in column_semantic_types):
        return types.DATASET_SPATIAL
    elif types.DATE_TIME in column_semantic_types:
        return types.DATASET_TEMPORAL
    elif types.CATEGORICAL in column_semantic_types:
        return types.DATASET_CATEGORICAL
    elif column_structural_type in (types.INTEGER, types.FLOAT):
        return types.DATASET_NUMERICAL
    else:
        return None
