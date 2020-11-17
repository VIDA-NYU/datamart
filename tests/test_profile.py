from datetime import datetime
from dateutil.tz import UTC
import io
import pandas
import random
import requests
import unittest
import textwrap

import datamart_geo
from datamart_profiler import process_dataset
from datamart_profiler.core import detect_language, expand_attribute_name
from datamart_profiler import profile_types
from datamart_profiler import spatial
from datamart_profiler.spatial import LATITUDE, LONGITUDE, LatLongColumn, \
    disambiguate_admin_areas
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


class TestNames(unittest.TestCase):
    def test_names(self):
        """Test expanding column names."""
        self.assertEqual(
            list(expand_attribute_name('Apt221bBakerStreet')),
            ['Apt', '221', 'b', 'Baker', 'Street'],
        )
        self.assertEqual(
            list(expand_attribute_name('place')),
            ['place'],
        )

    def test_duplicate_column_names(self):
        """Test reading a CSV with duplicate names."""
        metadata = process_dataset(io.StringIO(textwrap.dedent('''\
            one,two,one
            a,1,c
            d,2,f
        ''')))
        self.assertEqual(
            [col['name'] for col in metadata['columns']],
            ['one', 'two', 'one'],
        )


class TestIndex(unittest.TestCase):
    DATA = pandas.DataFrame({
        'a': [1, 1, 2, 2],
        'b': [3, 4, 5, 6],
        'c': [24, 35, 63, 57],
    })

    def test_no_index(self):
        df = self.DATA
        self.assertEqual(list(df.columns), ['a', 'b', 'c'])

        metadata = process_dataset(df)
        self.assertEqual(
            [col['name'] for col in metadata['columns']],
            ['a', 'b', 'c'],
        )

    def test_index(self):
        df = self.DATA.set_index(['a'])
        self.assertEqual(list(df.index.names), ['a'])
        self.assertEqual(list(df.columns), ['b', 'c'])

        metadata = process_dataset(df)
        self.assertEqual(
            [col['name'] for col in metadata['columns']],
            ['a', 'b', 'c'],
        )

    def test_multi_index(self):
        df = self.DATA.set_index(['a', 'b'])
        self.assertEqual(list(df.index.names), ['a', 'b'])
        self.assertEqual(list(df.columns), ['c'])

        metadata = process_dataset(df)
        self.assertEqual(
            [col['name'] for col in metadata['columns']],
            ['a', 'b', 'c'],
        )


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
        """Test pairing latitude and longitude columns by name or matching pairs defined by the user."""
        pairs, (missed_lat, missed_long) = spatial.pair_latlong_columns(
            [
                LatLongColumn(0, 'Pickup_latitude', None),
                LatLongColumn(1, 'lat', None),
                LatLongColumn(2, 'dropoff_latitude', None),
                LatLongColumn(3, 'latitude_place', None),
                LatLongColumn(4, 'la_coord', '1'),
            ],
            [
                LatLongColumn(5, 'long', None),
                LatLongColumn(6, 'dropoff_Longitude', None),
                LatLongColumn(7, 'pickup_longitude', None),
                LatLongColumn(8, 'other_Longitude', None),
                LatLongColumn(9, 'lo_coord', '1'),
            ],
        )
        self.assertEqual(
            pairs,
            [
                (LatLongColumn(1, 'lat', None),
                 LatLongColumn(5, 'long', None)),
                (LatLongColumn(2, 'dropoff_latitude', None),
                 LatLongColumn(6, 'dropoff_Longitude', None)),
                (LatLongColumn(0, 'Pickup_latitude', None),
                 LatLongColumn(7, 'pickup_longitude', None)),
                (LatLongColumn(4, 'la_coord', '1'),
                 LatLongColumn(9, 'lo_coord', '1')),
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
            [{k: v for k, v in c.items() if k != 'ranges'} for c in metadata['spatial_coverage']],
            [
                {'type': 'latlong', 'column_names': ['to lat', 'to long'], 'column_indexes': [2, 1]},
                {'type': 'latlong', 'column_names': ['from latitude', 'from longitude'], 'column_indexes': [0, 3]},
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
                'nb_columns': 2,
                'nb_temporal_columns': 1,
                'nb_numerical_columns': 1,
                'types': ['numerical', 'temporal'],
                'attribute_keywords': ['year', 'number'],
                'columns': [
                    {
                        'name': 'year',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': ['http://schema.org/DateTime'],
                        'unclean_values_ratio': 0.0,
                        'num_distinct_values': 3,
                        'mean': 1104508800.0,
                        'stddev': lambda n: round(n, 3) == 25784316.871,
                        'coverage': [
                            year_rng(1072915200.0),
                            year_rng(1104537600.0),
                            year_rng(1136073600.0),
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
        self.assertEqual(
            get_res([
                '2020-01-14T05:12:41',
                '2020-01-14T05:12:41',
            ]),
            'second',
        )
        self.assertEqual(
            get_res([
                '2020-01-14T05:12:00',
                '2020-01-14T05:12:00',
            ]),
            'minute',
        )
        self.assertEqual(
            get_res([
                '2020-01-14T00:00:00',
                '2020-01-14T00:00:00',
            ]),
            'day',
        )
        self.assertEqual(
            get_res([
                '2020-12',
                '2020-12',
            ]),
            'day',
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


class TestLanguages(unittest.TestCase):
    def test_single(self):
        languages = detect_language(['bonjour les amis', 'pain au chocolat'])
        languages = sorted(languages, key=lambda p: -p[1])
        lang0 = languages[0][0], round(languages[0][1], 2)
        self.assertEqual(
            lang0,
            ('fr', 1.0),
        )

    def test_mixed(self):
        languages = detect_language([
            'bonjour les amis',
            'october is my favorite month',
            'croissant',
        ])
        languages = list(languages)
        languages = sorted(languages, key=lambda p: -p[1])
        lang0 = languages[0][0], round(languages[0][1], 2)
        lang1 = languages[1][0], round(languages[1][1], 2)
        self.assertEqual(
            lang0,
            ('fr', 0.92),
        )
        self.assertEqual(
            lang1,
            ('en', 0.08),
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
                'nb_columns': 2,
                'nb_spatial_columns': 1,
                'average_row_size': lambda n: round(n, 2) == 47.33,
                'types': ['spatial'],
                'attribute_keywords': ['place', 'loc'],
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
                        'language': 'en',
                    },
                ],
                'spatial_coverage': [
                    {
                        'type': 'address',
                        'column_names': ['loc'],
                        'column_indexes': [1],
                        'ranges': check_geo_ranges(-74.00, 40.69, -73.98, 40.73),
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
        cls.geo_data = datamart_geo.GeoData.from_local_cache()

    def test_admin(self):
        with data('admins.csv', 'r') as data_fp:
            metadata = process_dataset(
                data_fp,
                geo_data=self.geo_data,
                coverage=True,
            )

        self.assertJson(
            metadata,
            {
                'size': 145,
                'nb_rows': 5,
                'nb_profiled_rows': 5,
                'nb_columns': 3,
                'nb_spatial_columns': 2,
                'average_row_size': lambda n: round(n, 2) == 29.0,
                'types': ['spatial'],
                'attribute_keywords': ['zero', 'one', 'mixed'],
                'columns': [
                    {
                        'name': 'zero',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [
                            'http://schema.org/AdministrativeArea',
                            'http://schema.org/Enumeration',
                        ],
                        'num_distinct_values': 3,
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
                    {
                        'name': 'mixed',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [],
                        'num_distinct_values': 5,
                    },
                ],
                'spatial_coverage': [
                    {
                        'type': 'admin',
                        'column_names': ['zero'],
                        'column_indexes': [0],
                        'ranges': [
                            {
                                'range': {
                                    'type': 'envelope',
                                    'coordinates': [
                                        [-61.797841, 55.065334],
                                        [55.854503, -21.370782],
                                    ],
                                },
                            },
                        ],
                    },
                    {
                        'type': 'admin',
                        'column_names': ['one'],
                        'column_indexes': [1],
                        'ranges': [
                            {
                                'range': {
                                    'type': 'envelope',
                                    'coordinates': [
                                        [8.97659, 50.56286],
                                        [13.81686, 47.27112],
                                    ],
                                },
                            },
                        ],
                    },
                ],
            },
        )

    def test_admin_ambiguous(self):
        def countries(areas):
            if areas and isinstance(areas[0], (list, set)):
                # Flatten
                areas = [area for candidates in areas for area in candidates]
            return {
                area.get_parent_area(datamart_geo.Type.COUNTRY).name
                for area in areas
            }

        # 'SC' resolves to states in both USA and Brazil
        sc_states = [
            area
            for area in self.geo_data.resolve_name_all('SC')
            if area.type == datamart_geo.Type.ADMIN_1
        ]
        if countries(sc_states) <= {
            'Federative Republic of Brazil', 'United States',
        }:
            raise ValueError("assumptions about geo data are wrong")
        # 'CT' resolves to states in both USA and India
        ct_states = [
            area
            for area in self.geo_data.resolve_name_all('CT')
            if area.type == datamart_geo.Type.ADMIN_1
        ]
        if countries(ct_states) <= {'Republic of India', 'United States'}:
            raise ValueError("assumptions about geo data are wrong")

        # Test that resolving those state names resolves to the USA
        resolved_areas = self.geo_data.resolve_names_all(['SC', 'CT', 'NY'])
        self.assertTrue(
            countries(resolved_areas) >= {
                'United States', 'Federative Republic of Brazil',
                'Republic of India',
            },
        )
        level, resolved_areas = disambiguate_admin_areas(resolved_areas)
        self.assertEqual(
            countries(resolved_areas),
            {'United States'},
        )
        self.assertEqual(level, 1)

    def test_point_wkt(self):
        with data('geo_wkt.csv', 'r') as data_fp:
            metadata = process_dataset(
                data_fp,
                coverage=True,
            )

        self.assertJson(
            metadata,
            {
                'types': ['numerical', 'spatial'],
                "size": 4708,
                "nb_rows": 100,
                "nb_profiled_rows": 100,
                "nb_columns": 3,
                "nb_spatial_columns": 1,
                "nb_numerical_columns": 1,
                "average_row_size": lambda n: round(n, 2) == 47.08,
                "attribute_keywords": ["id", "coords", "height"],
                "columns": [
                    {
                        "name": "id",
                        "structural_type": "http://schema.org/Text",
                        "semantic_types": [],
                        "missing_values_ratio": 0.01,
                        "num_distinct_values": 99
                    },
                    {
                        "name": "coords",
                        "structural_type": "http://schema.org/GeoCoordinates",
                        "semantic_types": [],
                        "unclean_values_ratio": 0.0,
                    },
                    {
                        "name": "height",
                        "structural_type": "http://schema.org/Float",
                        "semantic_types": [],
                        "unclean_values_ratio": 0.0,
                        "mean": lambda n: round(n, 3) == 47.827,
                        "stddev": lambda n: round(n, 2) == 21.28,
                        "coverage": check_ranges(1.0, 90.0),
                    }
                ],
                "spatial_coverage": [
                    {
                        "type": "point",
                        "column_names": ["coords"],
                        "column_indexes": [1],
                        "ranges": check_geo_ranges(-74.006, 40.6905, -73.983, 40.7352)
                    }
                ],
            },
        )

    def test_point_latlong(self):
        with data('geo_latlong.csv', 'r') as data_fp:
            metadata = process_dataset(
                data_fp,
                coverage=True,
            )

        self.assertJson(
            metadata,
            {
                'types': ['numerical', 'spatial'],
                "size": 4408,
                "nb_rows": 100,
                "nb_profiled_rows": 100,
                "nb_columns": 3,
                "nb_spatial_columns": 1,
                "nb_numerical_columns": 1,
                "average_row_size": lambda n: round(n, 2) == 44.08,
                "attribute_keywords": ["id", "coords", "height"],
                "columns": [
                    {
                        "name": "id",
                        "structural_type": "http://schema.org/Text",
                        "semantic_types": [],
                        "missing_values_ratio": 0.01,
                        "num_distinct_values": 99
                    },
                    {
                        "name": "coords",
                        "structural_type": "http://schema.org/GeoCoordinates_LatLong",
                        "semantic_types": [],
                        "unclean_values_ratio": 0.0,
                    },
                    {
                        "name": "height",
                        "structural_type": "http://schema.org/Float",
                        "semantic_types": [],
                        "unclean_values_ratio": 0.0,
                        "mean": lambda n: round(n, 3) == 47.827,
                        "stddev": lambda n: round(n, 2) == 21.28,
                        "coverage": check_ranges(1.0, 90.0),
                    }
                ],
                "spatial_coverage": [
                    {
                        "type": "point_latlong",
                        "column_names": ["coords"],
                        "column_indexes": [1],
                        "ranges": check_geo_ranges(-74.006, 40.6905, -73.983, 40.7352)
                    }
                ],
            },
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
