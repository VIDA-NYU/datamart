import contextlib
import os
import tempfile

from datamart_augmentation import join, union
from datamart_materialize import make_writer
from datamart_profiler import process_dataset

from .test_profile import check_ranges
from .utils import DataTestCase, data


@contextlib.contextmanager
def setup_augmentation(orig, aug):
    with data(orig) as d:
        orig_meta = process_dataset(d)
    with data(aug) as d:
        aug_meta = process_dataset(d)

    with tempfile.TemporaryDirectory() as tmp:
        result = os.path.join(tmp, 'result.csv')
        writer = make_writer(result)
        with data(orig) as orig_data:
            with data(aug) as aug_data:
                yield orig_data, aug_data, orig_meta, aug_meta, result, writer


class TestJoin(DataTestCase):
    def test_basic_join(self):
        """Simple join between integer keys."""
        with setup_augmentation('basic_aug.csv', 'basic.csv') as (
            orig_data, aug_data, orig_meta, aug_meta, result, writer,
        ):
            output_metadata = join(
                orig_data,
                aug_data,
                orig_meta,
                aug_meta,
                writer,
                [[0]],
                [[2]],
            )

            with open(result) as table:
                self.assertCsvEqualNoOrder(
                    table.read(),
                    'number,desk_faces,name,country,what',
                    [
                        '5,west,james,canada,False',
                        '4,south,john,usa,False',
                        '7,west,michael,usa,True',
                        '6,east,robert,usa,False',
                        '11,,christopher,canada,True',
                    ],
                )

        self.assertJson(
            output_metadata,
            {
                'size': 167,
                'columns': [
                    {
                        'name': 'number',
                        'structural_type': 'http://schema.org/Integer',
                        'semantic_types': [],
                        'unclean_values_ratio': 0.0,
                        'num_distinct_values': 5,
                        'mean': 6.6,
                        'stddev': lambda n: round(n, 3) == 2.417,
                        'coverage': check_ranges(4.0, 11.0),
                    },
                    {
                        'name': 'desk_faces',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [],
                        'missing_values_ratio': 0.2,
                        'num_distinct_values': 3,
                    },
                    {
                        'name': 'name',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [],
                    },
                    {
                        'name': 'country',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': ['http://schema.org/Enumeration'],
                    },
                    {
                        'name': 'what',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [
                            'http://schema.org/Boolean',
                            'http://schema.org/Enumeration',
                        ],
                    },
                ],
                'qualities': [
                    {
                        'qualName': 'augmentation_info',
                        'qualValueType': 'dict',
                        'qualValue': {
                            'new_columns': ['name', 'country', 'what'],
                            'removed_columns': [],
                            'nb_rows_before': 5,
                            'nb_rows_after': 5,
                            'augmentation_type': 'join',
                        },
                    },
                ],
            },
        )

    def test_agg_join(self):
        """Join with aggregation between integer keys."""
        with setup_augmentation('agg_aug.csv', 'agg.csv') as (
            orig_data, aug_data, orig_meta, aug_meta, result, writer,
        ):
            output_metadata = join(
                orig_data,
                aug_data,
                orig_meta,
                aug_meta,
                writer,
                [[0]],
                [[0]],
            )

            with open(result) as table:
                self.assertCsvEqualNoOrder(
                    table.read(),
                    'id,location,work,mean salary,sum salary,max salary,min salary',
                    [
                        '30,south korea,True,150.0,300.0,200.0,100.0',
                        '40,brazil,False,,,,',
                        '70,usa,True,600.0,600.0,600.0,600.0',
                        '80,canada,True,200.0,200.0,200.0,200.0',
                        '100,france,False,250.0,500.0,300.0,200.0',
                    ],
                )

        self.assertJson(
            output_metadata,
            {
                'size': 248,
                'columns': [
                    {
                        'name': 'id',
                        'structural_type': 'http://schema.org/Integer',
                        'semantic_types': ['http://schema.org/identifier'],
                        'unclean_values_ratio': 0.0,
                        'num_distinct_values': 5,
                        'mean': 64.0,
                        'stddev': lambda n: round(n, 3) == 25.768,
                        'coverage': check_ranges(30, 100),
                    },
                    {
                        'name': 'location',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [],
                        'num_distinct_values': 5,
                    },
                    {
                        'name': 'work',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [
                            'http://schema.org/Boolean',
                            'http://schema.org/Enumeration',
                        ],
                    },
                    {
                        'name': 'mean salary',
                        'structural_type': 'http://schema.org/Float',
                        'semantic_types': [],
                    },
                    {
                        'name': 'sum salary',
                        'structural_type': 'http://schema.org/Float',
                        'semantic_types': [],
                    },
                    {
                        'name': 'max salary',
                        'structural_type': 'http://schema.org/Integer',
                        'semantic_types': [],
                    },
                    {
                        'name': 'min salary',
                        'structural_type': 'http://schema.org/Integer',
                        'semantic_types': [],
                    },
                ],
                'qualities': [
                    {
                        'qualName': 'augmentation_info',
                        'qualValueType': 'dict',
                        'qualValue': {
                            'new_columns': [
                                'work', 'mean salary', 'sum salary',
                                'max salary', 'min salary',
                            ],
                            'removed_columns': [],
                            'nb_rows_before': 5,
                            'nb_rows_after': 5,
                            'augmentation_type': 'join',
                        },
                    },
                ],
            },
        )

    def test_agg_join_specific_functions(self):
        with setup_augmentation('agg_aug.csv', 'agg.csv') as (
            orig_data, aug_data, orig_meta, aug_meta, result, writer,
        ):
            output_metadata = join(
                orig_data,
                aug_data,
                orig_meta,
                aug_meta,
                writer,
                [[0]],
                [[0]],
                agg_functions={
                    'work': 'count',
                    'salary': ['first', 'sum', 'max'],
                },
            )

            with open(result) as table:
                self.assertCsvEqualNoOrder(
                    table.read(),
                    'id,location,count work,first salary,sum salary,max salary',
                    [
                        '30,south korea,2,200.0,300.0,200.0',
                        '40,brazil,1,,,',
                        '70,usa,2,,600.0,600.0',
                        '80,canada,1,200.0,200.0,200.0',
                        '100,france,2,300.0,500.0,300.0',
                    ],
                )

        self.assertJson(
            output_metadata,
            {
                'size': 197,
                'columns': [
                    {
                        'name': 'id',
                        'structural_type': 'http://schema.org/Integer',
                        'semantic_types': ['http://schema.org/identifier'],
                        'unclean_values_ratio': 0.0,
                        'num_distinct_values': 5,
                        'mean': 64.0,
                        'stddev': lambda n: round(n, 3) == 25.768,
                        'coverage': check_ranges(30, 100),
                    },
                    {
                        'name': 'location',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [],
                        'num_distinct_values': 5,
                    },
                    {
                        'name': 'count work',
                        'structural_type': 'http://schema.org/Integer',
                        'semantic_types': [],
                    },
                    {
                        'name': 'first salary',
                        'structural_type': 'http://schema.org/Integer',
                        'semantic_types': [],
                    },
                    {
                        'name': 'sum salary',
                        'structural_type': 'http://schema.org/Float',
                        'semantic_types': [],
                    },
                    {
                        'name': 'max salary',
                        'structural_type': 'http://schema.org/Integer',
                        'semantic_types': [],
                    },
                ],
                'qualities': [
                    {
                        'qualName': 'augmentation_info',
                        'qualValue': {
                            'new_columns': [
                                'count work', 'first salary',
                                'sum salary', 'max salary',
                            ],
                            'removed_columns': [],
                            'nb_rows_before': 5,
                            'nb_rows_after': 5,
                            'augmentation_type': 'join',
                        },
                        'qualValueType': 'dict',
                    },
                ],
            },
        )

    def test_geo_join(self):
        with setup_augmentation('geo_aug.csv', 'geo.csv') as (
            orig_data, aug_data, orig_meta, aug_meta, result, writer,
        ):
            output_metadata = join(
                orig_data,
                aug_data,
                orig_meta,
                aug_meta,
                writer,
                [[0, 1]],
                [[1, 2]],
            )

            with open(result) as table:
                table_lines = table.read().splitlines(False)
                # Truncate fields to work around rounding errors
                # FIXME: Deal with rounding errors
                table_lines = [
                    ','.join(
                        e[:8] if e[0] < 'a' or e[0] > 'z' else e
                        for e in line.split(',')
                    )
                    for line in table_lines
                ]
                self.assertCsvEqualNoOrder(
                    '\n'.join(table_lines[0:6]),
                    'lat,long,id,letter,id_r,mean height,sum height,max height,min height',
                    [
                        '40.73279,-73.9985,place100,a,'
                        + 'place00,50.24088,351.6862,85.77256,27.97864',
                        '40.72970,-73.9978,place101,b,'
                        + 'place01,42.57717,425.7717,67.62636,17.53429',
                        '40.73266,-73.9975,place102,c,'
                        + 'place06,50.03064,250.1532,79.72296,23.72270',
                        '40.73117,-74.0018,place103,d,'
                        + 'place08,49.40183,395.2146,84.19146,5.034845',
                        '40.69427,-73.9898,place104,e,'
                        + 'place59,47.73903,286.4341,93.16298,11.71055',
                    ],
                )

            self.assertJson(
                output_metadata,
                {
                    'size': 998,
                    'columns': [
                        {
                            'name': 'lat',
                            'structural_type': 'http://schema.org/Float',
                            'semantic_types': ['http://schema.org/latitude'],
                            'unclean_values_ratio': 0.0,
                            'mean': lambda n: round(n, 3) == 40.709,
                            'stddev': lambda n: round(n, 3) == 0.019,
                        },
                        {
                            'name': 'long',
                            'structural_type': 'http://schema.org/Float',
                            'semantic_types': ['http://schema.org/longitude'],
                            'unclean_values_ratio': 0.0,
                            'mean': lambda n: round(n, 3) == -73.993,
                            'stddev': lambda n: round(n, 5) == 0.00528,
                        },
                        {
                            'name': 'id',
                            'structural_type': 'http://schema.org/Text',
                            'semantic_types': [],
                            'num_distinct_values': 10,
                        },
                        {
                            'name': 'letter',
                            'structural_type': 'http://schema.org/Text',
                            'semantic_types': [],
                            'num_distinct_values': 10,
                        },
                        {
                            'name': 'id_r',
                            'structural_type': 'http://schema.org/Text',
                            'semantic_types': [],
                        },
                        {
                            'name': 'mean height',
                            'structural_type': 'http://schema.org/Float',
                            'semantic_types': [],
                        },
                        {
                            'name': 'sum height',
                            'structural_type': 'http://schema.org/Float',
                            'semantic_types': [],
                        },
                        {
                            'name': 'max height',
                            'structural_type': 'http://schema.org/Float',
                            'semantic_types': [],
                        },
                        {
                            'name': 'min height',
                            'structural_type': 'http://schema.org/Float',
                            'semantic_types': [],
                        },
                    ],
                    'qualities': [
                        {
                            'qualName': 'augmentation_info',
                            'qualValueType': 'dict',
                            'qualValue': {
                                'new_columns': [
                                    'id_r', 'mean height',
                                    'sum height', 'max height', 'min height',
                                ],
                                'removed_columns': [],
                                'nb_rows_before': 10,
                                'nb_rows_after': 10,
                                'augmentation_type': 'join',
                            },
                        },
                    ],
                },
            )

    def test_temporal_daily_join(self):
        with setup_augmentation('daily_aug.csv', 'daily.csv') as (
            orig_data, aug_data, orig_meta, aug_meta, result, writer,
        ):
            output_metadata = join(
                orig_data,
                aug_data,
                orig_meta,
                aug_meta,
                writer,
                [[0]],
                [[0]],
            )

            with open(result) as table:
                self.assertCsvEqualNoOrder(
                    table.read(),
                    'orig_date,n_people,rain',
                    [
                        '2019-04-28,3,yes',
                        '2019-04-29,5,yes',
                        '2019-04-30,0,yes',
                        '2019-05-01,1,no',
                        '2019-05-02,3,no',
                        '2019-05-03,2,yes',
                    ],
                )

        self.assertJson(
            output_metadata,
            {
                'size': 131,
                'columns': [
                    {
                        'name': 'orig_date',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': ['http://schema.org/DateTime'],
                        'num_distinct_values': 6,
                        'mean': 1556625600.0,
                        'stddev': lambda n: round(n, 3) == 147556.091,
                        'coverage': check_ranges(1556409600.0, 1556841600.0),
                        'temporal_resolution': 'day',
                    },
                    {
                        'name': 'n_people',
                        'structural_type': 'http://schema.org/Integer',
                        'semantic_types': [],
                        'unclean_values_ratio': 0.0,
                        'num_distinct_values': 5,
                        'mean': lambda n: round(n, 3) == 2.333,
                        'stddev': lambda n: round(n, 3) == 1.599,
                        'coverage': check_ranges(0, 5),
                    },
                    {
                        'name': 'rain',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [
                            'http://schema.org/Boolean',
                            'http://schema.org/Enumeration',
                        ],
                    },
                ],
                'qualities': [
                    {
                        'qualName': 'augmentation_info',
                        'qualValueType': 'dict',
                        'qualValue': {
                            'new_columns': ['rain'],
                            'removed_columns': [],
                            'nb_rows_before': 6,
                            'nb_rows_after': 6,
                            'augmentation_type': 'join',
                        },
                    },
                ],
            },
        )

    def test_temporal_hourly_join(self):
        with setup_augmentation('hourly_aug.csv', 'hourly.csv') as (
            orig_data, aug_data, orig_meta, aug_meta, result, writer,
        ):
            output_metadata = join(
                orig_data,
                aug_data,
                orig_meta,
                aug_meta,
                writer,
                [[0]],
                [[0]],
            )

            with open(result) as table:
                self.assertCsvEqualNoOrder(
                    table.read(),
                    'orig_date,color,rain',
                    [
                        '2019-06-13T01:00:00,blue,no',
                        '2019-06-13T02:00:00,blue,no',
                        '2019-06-13T03:00:00,green,no',
                        '2019-06-13T04:00:00,green,yes',
                        '2019-06-13T05:00:00,blue,no',
                    ],
                )

        self.assertJson(
            output_metadata,
            {
                'size': 170,
                'columns': [
                    {
                        'name': 'orig_date',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': ['http://schema.org/DateTime'],
                        'num_distinct_values': 5,
                        'mean': 1560394777.6,
                        'stddev': lambda n: round(n, 3) == 5104.874,
                        'coverage': check_ranges(1560387584.0, 1560402048.0),
                        'temporal_resolution': 'hour',
                    },
                    {
                        'name': 'color',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [],
                        'num_distinct_values': 2,
                    },
                    {
                        'name': 'rain',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [
                            'http://schema.org/Boolean',
                            'http://schema.org/Enumeration',
                        ],
                    },
                ],
                'qualities': [
                    {
                        'qualName': 'augmentation_info',
                        'qualValueType': 'dict',
                        'qualValue': {
                            'new_columns': ['rain'],
                            'removed_columns': [],
                            'nb_rows_before': 5,
                            'nb_rows_after': 5,
                            'augmentation_type': 'join',
                        },
                    },
                ],
            },
        )

    def test_temporal_hourly_days_join(self):
        """Join daily data with hourly (= aggregate down to daily)."""
        with setup_augmentation('hourly_aug_days.csv', 'hourly.csv') as (
            orig_data, aug_data, orig_meta, aug_meta, result, writer,
        ):
            output_metadata = join(
                orig_data,
                aug_data,
                orig_meta,
                aug_meta,
                writer,
                [[0]],
                [[0]],
            )

            with open(result) as table:
                self.assertCsvEqualNoOrder(
                    table.read(),
                    'orig_date,color,rain',
                    [
                        '2019-06-12,blue,no',
                        '2019-06-13,green,no',
                    ],
                )

        self.assertJson(
            output_metadata,
            {
                'size': 63,
                'columns': [
                    {
                        'name': 'orig_date',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': ['http://schema.org/DateTime'],
                        'num_distinct_values': 2,
                        'mean': 1560340800.0,
                        'stddev': 43200.0,
                        'coverage': check_ranges(1560297600.0, 1560384000.0),
                        'temporal_resolution': 'day',
                    },
                    {
                        'name': 'color',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [],
                        'num_distinct_values': 2,
                    },
                    {
                        'name': 'rain',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [
                            'http://schema.org/Boolean',
                            'http://schema.org/Enumeration',
                        ],
                    },
                ],
                'qualities': [
                    {
                        'qualName': 'augmentation_info',
                        'qualValueType': 'dict',
                        'qualValue': {
                            'new_columns': ['rain'],
                            'removed_columns': [],
                            'nb_rows_before': 2,
                            'nb_rows_after': 2,
                            'augmentation_type': 'join',
                        },
                    },
                ],
            },
        )

    def test_temporal_daily_hours_join(self):
        """Join hourly data with daily (= repeat for each hour)."""
        with setup_augmentation('daily_aug_hours.csv', 'daily.csv') as (
            orig_data, aug_data, orig_meta, aug_meta, result, writer,
        ):
            output_metadata = join(
                orig_data,
                aug_data,
                orig_meta,
                aug_meta,
                writer,
                [[0]],
                [[0]],
            )

            with open(result) as table:
                self.assertCsvEqualNoOrder(
                    table.read(),
                    'orig_date,n_people,rain',
                    [
                        '2019-04-25T21:00:00Z,3,yes',
                        '2019-04-26T01:00:00Z,5,no',
                        '2019-04-26T05:00:00Z,6,no',
                        '2019-04-26T09:00:00Z,7,no',
                        '2019-04-26T13:00:00Z,6,no',
                        '2019-04-26T17:00:00Z,8,no',
                        '2019-04-26T21:00:00Z,7,no',
                        '2019-04-27T01:00:00Z,0,yes',
                        '2019-04-27T05:00:00Z,1,yes',
                        '2019-04-27T09:00:00Z,0,yes',
                        '2019-04-27T13:00:00Z,3,yes',
                        '2019-04-27T17:00:00Z,0,yes',
                        '2019-04-27T13:00:00Z,0,yes',
                    ],
                )

        self.assertJson(
            output_metadata,
            {
                'size': 383,
                'columns': [
                    {
                        'name': 'orig_date',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': ['http://schema.org/DateTime'],
                        'num_distinct_values': 12,
                        # FIXME: Getting a numpy.bool_ here, don't know why
                        'mean': lambda n: round(float(n), 3) == 1556310203.077,
                        'stddev': 50783.72599785144,
                        'coverage': check_ranges(1556226048.0, 1556384384.0),
                        'temporal_resolution': 'hour',
                    },
                    {
                        'name': 'n_people',
                        'structural_type': 'http://schema.org/Integer',
                        'semantic_types': [],
                        'unclean_values_ratio': 0.0,
                        'num_distinct_values': 7,
                        'mean': lambda n: round(n, 3) == 3.538,
                        'stddev': lambda n: round(n, 3) == 2.977,
                        'coverage': check_ranges(0, 8),
                    },
                    {
                        'name': 'rain',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [
                            'http://schema.org/Boolean',
                            'http://schema.org/Enumeration',
                        ],
                    },
                ],
                'qualities': [
                    {
                        'qualName': 'augmentation_info',
                        'qualValueType': 'dict',
                        'qualValue': {
                            'new_columns': ['rain'],
                            'removed_columns': [],
                            'nb_rows_before': 13,
                            'nb_rows_after': 13,
                            'augmentation_type': 'join',
                        },
                    },
                ],
            },
        )

    def test_spatial_temporal(self):
        """Join on both space and time columns."""
        with setup_augmentation('spatiotemporal_aug.csv', 'spatiotemporal.csv') as (
            orig_data, aug_data, orig_meta, aug_meta, result, writer,
        ):
            output_metadata = join(
                orig_data,
                aug_data,
                orig_meta,
                aug_meta,
                writer,
                [[1, 2], [0]],
                [[1, 2], [0]],
                agg_functions={
                    'color': ['first', 'count'],
                },
            )

            with open(result) as table:
                self.assertCsvEqualNoOrder(
                    table.read(),
                    'date,latitude,longitude,first color,count color',
                    [
                        '2006-06-20T06:00:00,43.237,6.072,green,2',
                        '2006-06-20T06:00:00,43.238,6.072,red,1',
                        '2006-06-20T06:00:00,43.237,6.073,orange,2',
                        '2006-06-20T06:00:00,43.238,6.073,red,6',
                        '2006-06-20T07:00:00,43.237,6.072,orange,1',
                        '2006-06-20T07:00:00,43.238,6.072,,0',
                        '2006-06-20T07:00:00,43.237,6.073,yellow,4',
                        '2006-06-20T07:00:00,43.238,6.073,blue,4',
                        '2006-06-20T08:00:00,43.237,6.072,green,2',
                        '2006-06-20T08:00:00,43.238,6.072,green,2',
                        '2006-06-20T08:00:00,43.237,6.073,red,6',
                        '2006-06-20T08:00:00,43.238,6.073,green,2',
                    ],
                )

        self.assertJson(
            output_metadata,
            {
                'size': 544,
                'columns': [
                    {
                        'name': 'date',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': ['http://schema.org/DateTime'],
                        'num_distinct_values': 3,
                        'mean': 1150786816.0,
                        'stddev': lambda n: round(n, 3) == 2926.324,
                        'coverage': check_ranges(1150783232.0, 1150790400.0),
                        'temporal_resolution': 'hour',
                    },
                    {
                        'name': 'latitude',
                        'structural_type': 'http://schema.org/Float',
                        'semantic_types': ['http://schema.org/latitude'],
                        'unclean_values_ratio': 0.0,
                        'mean': lambda n: round(n, 3) == 43.238,
                        'stddev': lambda n: round(n, 5) == 0.00050,
                    },
                    {
                        'name': 'longitude',
                        'structural_type': 'http://schema.org/Float',
                        'semantic_types': ['http://schema.org/longitude'],
                        'unclean_values_ratio': 0.0,
                        'mean': lambda n: round(n, 3) == 6.073,
                        'stddev': lambda n: round(n, 5) == 0.00050,
                    },
                    {
                        'name': 'first color',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': ['http://schema.org/Enumeration'],
                    },
                    {
                        'name': 'count color',
                        'structural_type': 'http://schema.org/Integer',
                        'semantic_types': [],
                    },
                ],
                'qualities': [
                    {
                        'qualName': 'augmentation_info',
                        'qualValueType': 'dict',
                        'qualValue': {
                            'new_columns': ['first color', 'count color'],
                            'removed_columns': [],
                            'nb_rows_before': 12,
                            'nb_rows_after': 12,
                            'augmentation_type': 'join',
                        },
                    },
                ],
            },
        )


class TestUnion(DataTestCase):
    def test_geo_union(self):
        with setup_augmentation('geo_aug.csv', 'geo.csv') as (
            orig_data, aug_data, orig_meta, aug_meta, result, writer,
        ):
            output_metadata = union(
                orig_data,
                aug_data,
                orig_meta,
                aug_meta,
                writer,
                [[0], [1], [2]],
                [[1], [2], [0]],
            )

            with open(result) as table:
                table_lines = table.read().splitlines(False)
                # Truncate fields to work around rounding errors
                # FIXME: Deal with rounding errors
                table_lines = [
                    ','.join(e[:8] for e in line.split(','))
                    for line in table_lines
                ]
                self.assertCsvEqualNoOrder(
                    '\n'.join(table_lines[0:6]),
                    'lat,long,id,letter',
                    [
                        '40.73279,-73.9985,place100,a',
                        '40.72970,-73.9978,place101,b',
                        '40.73266,-73.9975,place102,c',
                        '40.73117,-74.0018,place103,d',
                        '40.69427,-73.9898,place104,e',
                    ],
                )

        self.assertJson(
            output_metadata,
            {
                'size': 3442,
                'columns': [
                    {
                        'name': 'lat',
                        'structural_type': 'http://schema.org/Float',
                        'semantic_types': ['http://schema.org/latitude'],
                    },
                    {
                        'name': 'long',
                        'structural_type': 'http://schema.org/Float',
                        'semantic_types': ['http://schema.org/longitude'],
                    },
                    {
                        'name': 'id',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [],
                    },
                    {
                        'name': 'letter',
                        'structural_type': 'http://schema.org/Text',
                        'semantic_types': [],
                    },
                ],
                'qualities': [
                    {
                        'qualName': 'augmentation_info',
                        'qualValueType': 'dict',
                        'qualValue': {
                            'new_columns': [],
                            'removed_columns': [],
                            'nb_rows_before': 10,
                            'nb_rows_after': 110,
                            'augmentation_type': 'union',
                        },
                    },
                ],
            },
        )
