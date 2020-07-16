from datetime import datetime
from dateutil.tz import UTC
import pandas
import random
import requests
import unittest
import textwrap

from datamart_geo import GeoData
from datamart_profiler import process_dataset
from datamart_profiler import profile_types
from datamart_profiler import spatial
from datamart_profiler.spatial import LATITUDE, LONGITUDE
from datamart_profiler.temporal import get_temporal_resolution, parse_date

from .utils import DataTestCase, data


def check_ranges(min_, max_):
    def check(ranges):
        assert 2 <= len(ranges) <= 3
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


class TestLatlongSelection(DataTestCase):
    def test_normalize_name(self):
        """Test normalizing column names."""
        self.assertEqual(
            spatial.normalize_latlong_column_name('latitude', LATITUDE),
            '',
        )
        self.assertEqual(
            spatial.normalize_latlong_column_name('Place_Latitude', LATITUDE),
            'place_',
        )
        self.assertEqual(
            spatial.normalize_latlong_column_name('start_Long_deg', LONGITUDE),
            'start__deg',
        )
        self.assertEqual(
            spatial.normalize_latlong_column_name('start_Lon_deg', LONGITUDE),
            'start__deg',
        )

    def test_pairing(self):
        """Test pairing latitude and longitude columns by name."""
        pairs, (missed_lat, missed_long) = spatial.pair_latlong_columns(
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
            missed_lat,
            ['latitude_place'],
        )
        self.assertEqual(
            missed_long,
            ['other_Longitude'],
        )

    def test_process(self):
        with data('lat_longs.csv', 'r') as data_fp:
            dataframe = pandas.read_csv(data_fp)
        metadata = process_dataset(
            dataframe,
        )
        # Check columns
        self.assertJson(
            [
                {k: v for k, v in c.items()
                 if k in ['name', 'structural_type', 'semantic_types']}
                for c in metadata['columns']],
            [
                {
                    'name': 'from latitude',
                    'structural_type': 'http://schema.org/Float',
                    'semantic_types': ['http://schema.org/latitude'],
                },
                {
                    'name': 'to long',
                    'structural_type': 'http://schema.org/Float',
                    'semantic_types': ['http://schema.org/longitude'],
                },
                {
                    'name': 'to lat',
                    'structural_type': 'http://schema.org/Float',
                    'semantic_types': ['http://schema.org/latitude'],
                },
                {
                    'name': 'from longitude',
                    'structural_type': 'http://schema.org/Float',
                    'semantic_types': ['http://schema.org/longitude'],
                },
                {
                    'name': 'unpaired lat',
                    'structural_type': 'http://schema.org/Float',
                    'semantic_types': [],
                },
            ]
        )
        # Check pairs
        self.assertJson(
            [
                {k: v for k, v in c.items() if k in ('lat', 'lon')}
                for c in metadata['spatial_coverage']
            ],
            [
                {'lat': 'to lat', 'lon': 'to long'},
                {'lat': 'from latitude', 'lon': 'from longitude'},
            ],
        )


class TestDates(DataTestCase):
    def test_parse(self):
        """Test parsing dates."""
        self.assertEqual(
            parse_date('Monday July 1, 2019'),
            datetime(2019, 7, 1, tzinfo=UTC),
        )
        self.assertEqual(
            parse_date('20190702T211319Z'),
            datetime(2019, 7, 2, 21, 13, 19, tzinfo=UTC),
        )
        dt = parse_date('2019-07-02T21:13:19-04:00')
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
            parse_date('2019-07-02 18:05 UTC'),
            datetime(2019, 7, 2, 18, 5, tzinfo=UTC),
        )
        self.assertEqual(
            parse_date('2019-07-02 18:05 L'),
            None,
        )

    def test_year(self):
        """Test the 'year' special-case."""
        dataframe = pandas.DataFrame({
            'year': [2004, 2005, 2006],
            'number': [2014, 2015, float('nan')],
        })
        metadata = process_dataset(dataframe)

        def year_rng(year):
            year = float(year)
            return {'range': {'gte': year, 'lte': year}}

        self.assertJson(
            metadata,
            {
                'nb_rows': 3,
                'nb_profiled_rows': 3,
                'columns': [
                    {
                        'name': 'year',
                        'structural_type': 'http://schema.org/Integer',
                        'semantic_types': ['http://schema.org/DateTime'],
                        'unclean_values_ratio': 0.0,
                        'num_distinct_values': 3,
                        'mean': 2005.0,
                        'stddev': lambda n: round(n, 3) == 0.816,
                        'coverage': [
                            year_rng(2004),
                            year_rng(2005),
                            year_rng(2006),
                        ],
                        'temporal_resolution': 'year',
                    },
                    {
                        'name': 'number',
                        'structural_type': 'http://schema.org/Integer',
                        'semantic_types': [],
                        'missing_values_ratio': lambda n: round(n, 2) == 0.33,
                        'unclean_values_ratio': 0.0,
                        'num_distinct_values': 2,
                        'mean': 2014.5,
                        'stddev': 0.5,
                        'coverage': [
                            {'range': {'gte': 2014.0, 'lte': 2014.0}},
                            {'range': {'gte': 2015.0, 'lte': 2015.0}},
                        ],
                    },
                ],
            },
        )


class TestTemporalResolutions(unittest.TestCase):
    def test_pandas(self):
        def get_res(values):
            idx = pandas.Index(pandas.to_datetime(values))
            return get_temporal_resolution(idx)

        self.do_checks(get_res)

    def test_native(self):
        def get_res(values):
            values = [parse_date(d) for d in values]
            return get_temporal_resolution(values)

        self.do_checks(get_res)

    def do_checks(self, get_res):
        self.assertEqual(
            get_res([
                '2020-01-14T21:05:02',
                '2020-01-14T21:05:07',
                '2020-01-14T21:05:30',
                '2020-01-14T22:21:54',
                '2020-01-15T05:12:41',
                '2020-03-21T05:12:41',
            ]),
            'second',
        )
        self.assertEqual(
            get_res([
                '2020-01-14T21:05:02',
                '2020-01-14T21:05:07',
                '2020-01-14T21:05:30',
            ]),
            'second',
        )
        self.assertEqual(
            get_res([
                '2020-01-14T21:05:00Z',
                '2020-01-14T21:05:00Z',
                '2020-01-14T21:05:00Z',
                '2020-01-14T21:21:00Z',
                '2020-01-15T05:12:00Z',
                '2020-03-21T05:12:00Z',
                '2020-03-21T05:13:00Z',
            ]),
            'minute',
        )
        self.assertEqual(
            get_res([
                '2020-01-14T21:30:00',
                '2020-01-14T21:30:00',
                '2020-01-14T21:30:00',
                '2020-01-14T22:31:07',
                '2020-01-15T05:29:59',
                '2020-03-21T05:29:59',
            ]),
            'hour',
        )
        self.assertEqual(
            get_res([
                '2020-01-14',
                '2020-01-14',
                '2020-01-14',
                '2020-01-14',
                '2020-01-15',
                '2020-03-21',
            ]),
            'day',
        )
        self.assertEqual(
            get_res([
                '2020-01-14T05:12:41',
                '2020-01-14T05:12:41',
                '2020-01-14T05:12:41',
                '2020-01-14T05:12:41',
                '2020-01-15T05:13:03',
                '2020-01-16T05:12:41',
                '2020-03-21T05:12:41',
            ]),
            'day',
        )
        self.assertEqual(
            get_res([
                '2020-01-30',
                '2020-01-30',
                '2020-02-13',
                '2020-02-21',
                '2020-03-05',
                '2020-03-05',
                '2020-03-12',
            ]),
            'week',
        )
        self.assertEqual(
            get_res([
                '2020-02-06',
                '2020-02-06',
                '2020-03-07',
                '2020-05-07',
                '2020-06-06',
            ]),
            'month',
        )
        self.assertEqual(
            get_res([
                '2017-02-06',
                '2018-03-06',
                '2018-03-06',
                '2020-02-04',
            ]),
            'year',
        )
        self.assertEqual(
            get_res([
                '2017-01-01',
                '2018-04-01',
                '2018-07-01',
                '2018-12-31',
            ]),
            'quarter',
        )


class TestTypes(unittest.TestCase):
    def do_test(self, match, positive, negative):
        for elem in textwrap.dedent(positive).splitlines():
            if elem:
                self.assertTrue(match(elem),
                                "Didn't match: %s" % elem)
        for elem in textwrap.dedent(negative).splitlines():
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

    def test_geo_combined(self):
        positive = '''\
        70 WASHINGTON SQUARE S, NEW YORK, NY 10012 (40.729753, -73.997174)
        R\u00C9MI'S HOUSE, BROOKLYN, NY (40.729753, -73.997174)
        '''
        negative = '''\
        70 Washington Square (40.729753, -73.997174)
        '''
        self.do_test(
            profile_types._re_geo_combined.match,
            positive, negative,
        )


class TestTruncate(unittest.TestCase):
    def test_simple(self):
        from datamart_profiler.core import truncate_string

        self.assertEqual(truncate_string("abc", 10), "abc")
        self.assertEqual(truncate_string("abcdefghij", 10), "abcdefghij")
        self.assertEqual(truncate_string("abcdefghijk", 10), "abcdefg...")
        self.assertEqual(truncate_string("abcdefghijklmnop", 10), "abcdefg...")

    def test_words(self):
        from datamart_profiler.core import truncate_string

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

        def replacement(url, *, q):
            if not replacement.failed:  # Fail just once
                replacement.failed = True
                response = requests.Response()
                response.status_code = 500
                raise requests.HTTPError("Fake 500 error", response=response)
            return [queries[qe] for qe in q]
        replacement.failed = False

        old_query = spatial.nominatim_query
        old_min_batch_size = spatial.NOMINATIM_MIN_SPLIT_BATCH_SIZE
        spatial.nominatim_query = replacement
        spatial.NOMINATIM_MIN_SPLIT_BATCH_SIZE = 2
        try:
            with data('addresses.csv', 'r') as data_fp:
                metadata = process_dataset(
                    data_fp,
                    nominatim='http://nominatim/',
                    coverage=True,
                )
        finally:
            spatial.nominatim_query = old_query
            spatial.NOMINATIM_MIN_SPLIT_BATCH_SIZE = old_min_batch_size

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
                        'geohashes4': [
                            {'hash': '1211302313301103', 'number': 1},
                            {'hash': '1211302313301102', 'number': 1},
                            {'hash': '1211302313300022', 'number': 1},
                        ],
                    },
                ],
            },
        )

    def test_querying(self):
        queries = {
            'a': [{'lat': 11.0, 'lon': 12.0}],
            'b': [],
            'c': [{'lat': 31.0, 'lon': 32.0}],
        }
        old_query = spatial.nominatim_query
        spatial.nominatim_query = \
            lambda url, *, q: [queries[qe] for qe in q]
        try:
            res, empty = spatial.nominatim_resolve_all(
                'http://240.123.45.67:21',
                ['a', 'b', 'c', 'b', 'b', 'c'],
            )
            self.assertEqual(
                res,
                [
                    (11.0, 12.0),
                    (31.0, 32.0),
                    (31.0, 32.0),
                ],
            )
            self.assertEqual(empty, 6)

            res, empty = spatial.nominatim_resolve_all(
                'http://240.123.45.67:21',
                [
                    'a', 'b', 'c', 'b', 'b', 'c', 'a', 'b', 'c',
                    # Second batch
                    'b', 'b', 'c',
                ],
            )
            self.assertEqual(
                res,
                [
                    (11.0, 12.0),
                    (11.0, 12.0),
                    (31.0, 32.0),
                    (31.0, 32.0),
                    (31.0, 32.0),
                    # Second batch
                    (31.0, 32.0),
                ],
            )
            self.assertEqual(empty, 12)
        finally:
            spatial.nominatim_query = old_query


class TestGeo(DataTestCase):
    @classmethod
    def setUpClass(cls):
        cls.geo_data = GeoData.from_local_cache()
        cls.geo_data.load_areas([0, 1], bounds=True)

    def test_profile(self):
        with data('admins.csv', 'r') as data_fp:
            metadata = process_dataset(
                data_fp,
                geo_data=self.geo_data,
                coverage=True,
            )

        self.assertJson(
            metadata,
            {
                'size': 93,
                'nb_rows': 5,
                'nb_profiled_rows': 5,
                'columns': [
                    {
                        'name': 'zero',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [
                            'http://schema.org/AdministrativeArea',
                            'http://schema.org/Enumeration',
                        ],
                        'num_distinct_values': 2,
                        'admin_area_level': 0,
                    },
                    {
                        'name': 'one',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [
                            'http://schema.org/AdministrativeArea',
                            'http://schema.org/Enumeration',
                        ],
                        'num_distinct_values': 5,
                        'admin_area_level': 1,
                    },
                ],
                'spatial_coverage': [
                    {
                        'admin': 'zero',
                        'ranges': [
                            {
                                'range': {
                                    'type': 'envelope',
                                    'coordinates': [
                                        [-61.79784095, 55.065334377],
                                        [55.8545028, -21.370782159],
                                    ],
                                },
                            },
                        ],
                    },
                    {
                        'admin': 'one',
                        'ranges': [
                            {
                                'range': {
                                    'type': 'envelope',
                                    'coordinates': [
                                        [-61.79784, 53.72778],
                                        [13.81686, 14.40811],
                                    ],
                                },
                            },
                        ],
                    },
                ],
            },
        )


class TestGeoHash(unittest.TestCase):
    def test_bit_encoding(self):
        self.assertEqual(
            spatial.bits_to_chars(
                [0, 1, 1, 0, 0, 1, 0, 1, 1, 1, 0, 0, 1, 0, 1],
                base_bits=5,
            ),
            'dr5',
        )
        self.assertEqual(
            spatial.bits_to_chars(
                [1, 1, 0, 1, 0, 0, 0, 0, 0, 0],
                base_bits=2,
            ),
            '31000',
        )

    def test_bit_decoding(self):
        self.assertEqual(
            list(spatial.chars_to_bits('dr5', base_bits=5)),
            [0, 1, 1, 0, 0, 1, 0, 1, 1, 1, 0, 0, 1, 0, 1],
        )
        self.assertEqual(
            list(spatial.chars_to_bits('31000', base_bits=2)),
            [1, 1, 0, 1, 0, 0, 0, 0, 0, 0],
        )

    def test_geohash32(self):
        self.assertEqual(
            spatial.hash_location((40.6962574, -73.9849621)),
            'dr5rs2tbckjz3h8c',
        )
        self.assertEqual(
            spatial.hash_location(
                (48.8588376, 2.2768489),
                base=32, precision=5,
            ),
            'u09tg',
        )

    def test_geohash32_reverse(self):
        self.assertEqual(
            spatial.decode_hash('dr5rs2tbckjz3h8c'),
            (
                40.696257399849856, 40.696257400013565,
                -73.98496210011217, -73.98496209978475,
            ),
        )
        self.assertEqual(
            spatial.decode_hash('u09tg', base=32),
            (
                48.8232421875, 48.8671875,
                2.2412109375, 2.28515625,
            ),
        )

    def test_geohash4(self):
        self.assertEqual(
            spatial.hash_location(
                (40.6962574, -73.9849621),
                base=4,
            ),
            '1211302313300023',
        )
        self.assertEqual(
            spatial.hash_location(
                (48.8588376, 2.2768489),
                base=4, precision=5,
            ),
            '31000',
        )

    def test_geohash4_reverse(self):
        self.assertEqual(
            spatial.decode_hash('1211302313300023', base=4),
            (
                40.69610595703125, 40.6988525390625,
                -73.9874267578125, -73.98193359375,
            ),
        )
        self.assertEqual(
            spatial.decode_hash('31000', base=4),
            (
                45.0, 50.625,
                0.0, 11.25,
            ),
        )

    def test_sketch_hashes(self):
        test_data = [
            ((40.0, 10.0), '3011'),
            ((20.0, 12.0), '3001'),
            ((30.0, 50.0), '3030'),
            ((18.0, 80.0), '3023'),
            ((15.0, -170.0), '1001'),
            ((14.0, -168.0), '1001'),
            ((-75.0, -50.0), '0203'),
            ((-76.0, -18.0), '0223'),
            ((-74.0, -16.0), '0223'),
            ((-75.0, -17.0), '0223'),
        ]
        points = [p[0] for p in test_data]
        self.assertEqual(
            [
                spatial.hash_location(point, base=4, precision=4)
                for point in points
            ],
            [p[1] for p in test_data],
        )
        self.assertEqual(
            spatial.get_geohashes(
                points,
                base=4,
                precision=4,  # Big enough it can fail, low enough I can debug
                number=3,
            ),
            [('30', 4), ('10', 2), ('02', 4)],
        )

        self.assertEqual(
            spatial.get_geohashes(
                [(1.0, 1.0), (46.0, 91.0), (44.0, 91.0), (89.0, 89.0)],
                base=4,
                precision=3,
                number=3,
            ),
            [('3', 4)],
        )

        self.assertEqual(
            spatial.get_geohashes(
                [(12.0, 12.0), (12.0, -12.0), (-12.0, -12.0), (-12.0, 12.0)],
                base=4,
                precision=3,
                number=3,
            ),
            [('', 4)],
        )


class TestMedianDist(unittest.TestCase):
    def test_median_dist(self):
        points = []

        def make_grid(mx, my):
            for y in range(-100, 100):
                for x in range(-100, 100):
                    points.append((
                        mx + x + random.random() * 0.2,
                        my + y + random.random() * 0.2,
                    ))
        make_grid(0, 0)
        make_grid(500, 0)
        make_grid(-200, 300)

        self.assertAlmostEqual(
            spatial.median_smallest_distance(points),
            0.9,
            delta=0.05
        )
