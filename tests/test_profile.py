from datetime import datetime
from dateutil.tz import UTC
import os
import unittest
from unittest.mock import call, patch

import datamart_profiler
from datamart_profiler import pair_latlong_columns, \
    normalize_latlong_column_name
from datamart_profiler import profile_types

from .utils import DataTestCase


def check_ranges(min_, max_):
    def check(ranges):
        assert len(ranges) == 3
        for rg in ranges:
            assert rg.keys() == {'range'}
            rg = rg['range']
            assert rg.keys() == {'gte', 'lte'}
            gte, lte = rg['gte'], rg['lte']
            assert min_ <= gte <= lte <= max_

        return True

    return check


def check_geo_ranges(min_long, min_lat, max_long, max_lat):
    def check(ranges):
        assert len(ranges) == 3
        for rg in ranges:
            assert rg.keys() == {'range'}
            rg = rg['range']
            assert rg.keys() == {'type', 'coordinates'}
            assert rg['type'] == 'envelope'
            [long1, lat1], [long2, lat2] = rg['coordinates']
            assert min_lat <= lat2 <= lat1 <= max_lat
            assert min_long <= long1 <= long2 <= max_long

        return True

    return check


def check_plot(kind):
    def check(plot):
        return plot['type'] == kind

    return check


class TestLatlongSelection(unittest.TestCase):
    def test_normalize_name(self):
        """Test normalizing column names."""
        self.assertEqual(
            normalize_latlong_column_name('latitude', 'latitude', 'lat'),
            '',
        )
        self.assertEqual(
            normalize_latlong_column_name('Place_Latitude', 'latitude', 'lat'),
            'place_',
        )
        self.assertEqual(
            normalize_latlong_column_name('start_Lat_deg', 'latitude', 'lat'),
            'start__deg',
        )

    def test_pairing(self):
        """Test pairing latitude and longitude columns by name."""
        with patch('datamart_profiler.logger') as mock_warn:
            pairs = pair_latlong_columns(
                [
                    ('Pickup_latitude', 1),
                    ('lat', 7),
                    ('dropoff_latitude', 2),
                    ('latitude_place', 8),
                ],
                [
                    ('long', 5),
                    ('dropoff_Longitude', 3),
                    ('pickup_longitude', 4),
                    ('other_Longitude', 6),
                ],
            )
        self.assertEqual(
            pairs,
            [
                (('lat', 7), ('long', 5)),
                (('dropoff_latitude', 2), ('dropoff_Longitude', 3)),
                (('Pickup_latitude', 1), ('pickup_longitude', 4)),
            ],
        )
        self.assertEqual(
            mock_warn.warning.call_args_list,
            [
                call("Unmatched latitude columns: %r", ['latitude_place']),
                call("Unmatched longitude columns: %r", ['other_Longitude']),
            ],
        )


class TestDates(unittest.TestCase):
    def test_parse(self):
        """Test parsing dates."""
        self.assertEqual(
            profile_types.parse_date('Monday July 1, 2019'),
            datetime(2019, 7, 1, tzinfo=UTC),
        )
        self.assertEqual(
            profile_types.parse_date('20190702T211319Z'),
            datetime(2019, 7, 2, 21, 13, 19, tzinfo=UTC),
        )
        dt = profile_types.parse_date('2019-07-02T21:13:19-04:00')
        self.assertEqual(
            dt.replace(tzinfo=None),
            datetime(2019, 7, 2, 21, 13, 19),
        )
        self.assertEqual(
            dt.astimezone(UTC),
            datetime(2019, 7, 3, 1, 13, 19, tzinfo=UTC),
        )

        # Check that unknown timezones are not accepted
        self.assertEqual(
            profile_types.parse_date('2019-07-02 18:05 UTC'),
            datetime(2019, 7, 2, 18, 5, tzinfo=UTC),
        )
        self.assertEqual(
            profile_types.parse_date('2019-07-02 18:05 L'),
            None,
        )


class TestTypes(unittest.TestCase):
    def do_test(self, match, positive, negative):
        for elem in positive.splitlines():
            elem = elem.strip()
            if elem:
                self.assertTrue(match(elem),
                                "Didn't match: %s" % elem)
        for elem in negative.splitlines():
            elem = elem.strip()
            if elem:
                self.assertFalse(match(elem),
                                 "Shouldn't have matched: %s" % elem)

    def test_ints(self):
        positive = '''\
        12
        0
        +478
        -17
        '''
        negative = '''\
        1.7
        7A
        ++2
        --34
        +-7
        -+18
        '''
        self.do_test(
            profile_types._re_int.match,
            positive, negative,
        )
        self.assertFalse(profile_types._re_int.match(''))

    def test_floats(self):
        positive = '''\
        12.
        0.
        .7
        123.456
        +123.456
        +.456
        -.4
        .4e17
        -.4e17
        +8.4e17
        +8.e17
        '''
        negative = '''\
        1.7.3
        .7.3
        7.3.
        .
        -.
        +.
        7.A
        .e8
        8e17
        1.3e
        '''
        self.do_test(
            profile_types._re_float.match,
            positive, negative,
        )
        self.assertFalse(profile_types._re_float.match(''))


class TestTruncate(unittest.TestCase):
    def test_simple(self):
        from datamart_profiler import truncate_string

        self.assertEqual(truncate_string("abc", 10), "abc")
        self.assertEqual(truncate_string("abcdefghij", 10), "abcdefghij")
        self.assertEqual(truncate_string("abcdefghijk", 10), "abcdefg...")
        self.assertEqual(truncate_string("abcdefghijklmnop", 10), "abcdefg...")

    def test_words(self):
        from datamart_profiler import truncate_string

        self.assertEqual(
            truncate_string("abcde fghijklmnopqrs tuvwxyzABCD EF", 30),
            "abcde fghijklmnopqrs...",
        )
        self.assertEqual(
            truncate_string("abcde fghijklmnopqrs tu vwxyzABCD EF", 30),
            "abcde fghijklmnopqrs tu...",
        )
        self.assertEqual(
            truncate_string("abc defghijklmnopqrstuvwxyzABCDEFGHI", 30),
            "abc defghijklmnopqrstuvwxyz...",
        )


class TestNominatim(DataTestCase):
    def test_profile(self):
        old_query = datamart_profiler.nominatim_query
        queries = {
            "70 Washington Square S, New York, NY 10012": [{
                'lat': 40.7294, 'lon': -73.9972,
            }],
            "6 MetroTech, Brooklyn, NY 11201": [{
                'lat': 40.6944, 'lon': -73.9857,
            }],
            "251 Mercer St, New York, NY 10012": [{
                'lat': 40.7287, 'lon': -73.9957,
            }],
        }
        datamart_profiler.nominatim_query = lambda url, *, q: queries[q]
        try:
            data_dir = os.path.join(os.path.dirname(__file__), 'data')
            with open(os.path.join(data_dir, 'addresses.csv')) as data:
                metadata = datamart_profiler.process_dataset(
                    data,
                    nominatim='http://nominatim/',
                    coverage=True,
                )
        finally:
            datamart_profiler.nominatim_query = old_query

        self.assertJson(
            metadata,
            {
                'size': 142,
                'nb_rows': 3,
                'nb_profiled_rows': 3,
                'columns': [
                    {
                        'name': 'place',
                        'num_distinct_values': 3,
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [],
                    },
                    {
                        'name': 'loc',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [
                            'http://schema.org/Text',
                            'http://schema.org/address',
                        ],
                    },
                ],
                'spatial_coverage': [
                    {
                        'address': 'loc',
                        'ranges': check_geo_ranges(-74.00, 40.69, -73.98, 40.73),
                    },
                ],
            },
        )
