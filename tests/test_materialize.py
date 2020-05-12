import copy
import io
import json
import os
import shutil
import tempfile
import unittest

from datamart_materialize.d3m import D3mWriter, _D3mAddIndex
from datamart_materialize.pivot import pivot_table

from .utils import data


basic_metadata = {
    'name': 'basic',
    "size": 425,
    'columns': [
        {
            "name": "name",
            "structural_type": "http://schema.org/Text",
            "semantic_types": [],
        },
        {
            "name": "country",
            "structural_type": "http://schema.org/Text",
            "semantic_types": ["http://schema.org/Enumeration"],
        },
        {
            "name": "number",
            "structural_type": "http://schema.org/Integer",
            "semantic_types": [],
        },
        {
            "name": "what",
            "structural_type": "http://schema.org/Text",
            "semantic_types": [
                "http://schema.org/Boolean",
                "http://schema.org/Enumeration"
            ],
        }
    ],
}


basic_d3m_metadata = {
    'about': {
        'datasetID': 'test1',
        'datasetName': 'basic',
        'datasetSchemaVersion': '4.0.0',
        'datasetVersion': '1.0',
        'approximateSize': '425 B',
        'license': 'unknown',
        'redacted': False,
    },
    'dataResources': [
        {
            'resID': 'learningData',
            'resType': 'table',
            'resFormat': {'text/csv': ['csv']},
            'resPath': 'tables/learningData.csv',
            'isCollection': False,
            'columns': [
                {
                    'colIndex': 0,
                    'colName': 'name',
                    'colType': 'string',
                    'role': ['attribute'],
                },
                {
                    'colIndex': 1,
                    'colName': 'country',
                    'colType': 'categorical',
                    'role': ['attribute'],
                },
                {
                    'colIndex': 2,
                    'colName': 'number',
                    'colType': 'integer',
                    'role': ['attribute'],
                },
                {
                    'colIndex': 3,
                    'colName': 'what',
                    'colType': 'boolean',
                    'role': ['attribute'],
                },
            ],
        },
    ],
}


basic_d3m_metadata_with_index = copy.deepcopy(basic_d3m_metadata)
basic_d3m_metadata_with_index['dataResources'][0]['columns'] = (
    [{
        'colIndex': 0,
        'colName': 'd3mIndex',
        'colType': 'integer',
        'role': ['index'],
    }] +
    [
        dict(col, colIndex=col['colIndex'] + 1)
        for col in basic_d3m_metadata['dataResources'][0]['columns']
    ]
)


class TestD3m(unittest.TestCase):
    def _check_output(self, target, *,
                      metadata=basic_d3m_metadata, data_path='basic.csv'):
        with open(
            os.path.join(target, 'tables', 'learningData.csv'), 'rb'
        ) as fp:
            with data(data_path) as f_ref:
                self.assertEqual(
                    fp.read(),
                    f_ref.read(),
                )
        with open(
            os.path.join(target, 'datasetDoc.json'), 'r'
        ) as fp:
            self.assertEqual(
                json.load(fp),
                metadata,
            )

    def test_writer_default(self):
        """Test writing with default parameters."""
        with tempfile.TemporaryDirectory() as temp:
            target = os.path.join(temp, 'dataset')
            writer = D3mWriter('test1', target, basic_metadata)
            with data('basic.csv') as f_in, writer.open_file() as f_out:
                shutil.copyfileobj(f_in, f_out)
            writer.finish()

            self._check_output(target)

    def test_d3m_writer_4(self):
        """Test writing in 4.0.0 format explicitely."""
        with tempfile.TemporaryDirectory() as temp:
            target = os.path.join(temp, 'dataset')
            writer = D3mWriter('test1', target, basic_metadata,
                               format_options={'version': '4.0.0'})
            with data('basic.csv') as f_in, writer.open_file() as f_out:
                shutil.copyfileobj(f_in, f_out)
            writer.finish()

            self._check_output(target)

    def test_writer_32(self):
        """Test writing in 3.2.0 format."""
        with tempfile.TemporaryDirectory() as temp:
            target = os.path.join(temp, 'dataset')
            writer = D3mWriter('test1', target, basic_metadata,
                               format_options={'version': '3.2.0'})
            with data('basic.csv') as f_in, writer.open_file() as f_out:
                shutil.copyfileobj(f_in, f_out)
            writer.finish()

            meta = copy.deepcopy(basic_d3m_metadata)
            meta['about']['datasetSchemaVersion'] = '3.2.0'
            meta['dataResources'][0]['resFormat'] = ['text/csv']
            self._check_output(target, metadata=meta)

    def test_index_add(self):
        """Test adding the index to data that doesn't have it."""
        with tempfile.TemporaryDirectory() as temp:
            target = os.path.join(temp, 'dataset')
            writer = D3mWriter('test1', target, basic_metadata,
                               format_options={'need_d3mindex': True})
            with data('basic.csv') as f_in, writer.open_file() as f_out:
                shutil.copyfileobj(f_in, f_out)
            writer.finish()

            self._check_output(
                target,
                metadata=basic_d3m_metadata_with_index,
                data_path='basic.d3m.csv',
            )

    def test_index_present(self):
        """Test that requiring an index doesn't add one if already there."""
        with tempfile.TemporaryDirectory() as temp:
            target = os.path.join(temp, 'dataset')
            writer = D3mWriter('test1', target, basic_metadata,
                               format_options={'need_d3mindex': True})
            with data('basic.d3m.csv') as f_in, writer.open_file() as f_out:
                shutil.copyfileobj(f_in, f_out)
            writer.finish()

            self._check_output(
                target,
                metadata=basic_d3m_metadata_with_index,
                data_path='basic.d3m.csv',
            )


class StringIO(io.StringIO):
    """Version of StringIO that doesn't throw away the buffer on close().
    """
    actually_closed = False

    def write(self, buf):
        if self.actually_closed:
            raise ValueError("I/O operation on closed file")
        return super(StringIO, self).write(buf)

    def close(self):
        self.actually_closed = True


class TestD3mIndexAdder(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._orig_buffer_max = _D3mAddIndex.BUFFER_MAX
        _D3mAddIndex.BUFFER_MAX = 10  # 10 bytes or characters

    @classmethod
    def tearDownClass(cls) -> None:
        _D3mAddIndex.BUFFER_MAX = cls._orig_buffer_max

    def test_add_binary(self):
        """Test adding d3mIndex with binary=True."""
        dest = StringIO()
        adapter = _D3mAddIndex(dest, True)
        self.assertIs(adapter._generate, None)
        adapter.write(b'id,te')
        self.assertIs(adapter._generate, None)
        adapter.write(b'xt\n123')
        self.assertIs(adapter._generate, True)
        self.assertEqual(dest.getvalue(), 'd3mIndex,id,text\r\n')
        adapter.write(b',hello world\n4')
        self.assertEqual(
            dest.getvalue(),
            'd3mIndex,id,text\r\n0,123,hello world\r\n',
        )
        adapter.write(b'56,some more\n1348,text values\n17,with ids')
        self.assertEqual(
            dest.getvalue(),
            'd3mIndex,id,text\r\n0,123,hello world\r\n1,456,some more\r\n' +
            '2,1348,text values\r\n',
        )
        adapter.close()
        self.assertEqual(
            dest.getvalue(),
            'd3mIndex,id,text\r\n0,123,hello world\r\n1,456,some more\r\n' +
            '2,1348,text values\r\n3,17,with ids\r\n',
        )

    def test_add_text(self):
        """Test adding d3mIndex with binary=False."""
        dest = StringIO()
        adapter = _D3mAddIndex(dest, False)
        self.assertIs(adapter._generate, None)
        adapter.write('id,te')
        self.assertIs(adapter._generate, None)
        adapter.write('xt\n123')
        self.assertIs(adapter._generate, True)
        self.assertEqual(dest.getvalue(), 'd3mIndex,id,text\r\n')
        adapter.write(',hello world\n4')
        self.assertEqual(
            dest.getvalue(),
            'd3mIndex,id,text\r\n0,123,hello world\r\n',
        )
        adapter.write('56,some more\n1348,text values\n17,with ids')
        self.assertEqual(
            dest.getvalue(),
            'd3mIndex,id,text\r\n0,123,hello world\r\n1,456,some more\r\n' +
            '2,1348,text values\r\n',
        )
        adapter.close()
        self.assertEqual(
            dest.getvalue(),
            'd3mIndex,id,text\r\n0,123,hello world\r\n1,456,some more\r\n' +
            '2,1348,text values\r\n3,17,with ids\r\n',
        )

    def test_passthrough_binary(self):
        """Test passthrough with binary=True."""
        dest = StringIO()
        adapter = _D3mAddIndex(dest, True)
        self.assertIs(adapter._generate, None)
        adapter.write(b'd3mIndex,')
        self.assertIs(adapter._generate, None)
        adapter.write(b'text\n123')
        self.assertIs(adapter._generate, False)
        adapter.write(b',hello world\n4')
        self.assertEqual(
            dest.getvalue(),
            'd3mIndex,text\n123,hello world\n4',
        )
        adapter.write(b'56,some more\n1348,text values\n17,with ids')
        adapter.close()
        self.assertEqual(
            dest.getvalue(),
            'd3mIndex,text\n123,hello world\n456,some more\n' +
            '1348,text values\n17,with ids',
        )

    def test_passthrough_text(self):
        """Test passthrough with binary=False."""
        dest = StringIO()
        adapter = _D3mAddIndex(dest, False)
        self.assertIs(adapter._generate, None)
        adapter.write('d3mIndex,')
        self.assertIs(adapter._generate, None)
        adapter.write('text\n123')
        self.assertIs(adapter._generate, False)
        adapter.write(',hello world\n4')
        self.assertEqual(
            dest.getvalue(),
            'd3mIndex,text\n123,hello world\n4',
        )
        adapter.write('56,some more\n1348,text values\n17,with ids')
        adapter.close()
        self.assertEqual(
            dest.getvalue(),
            'd3mIndex,text\n123,hello world\n456,some more\n' +
            '1348,text values\n17,with ids',
        )


class TestConvert(unittest.TestCase):
    def test_pivot(self):
        f_out = io.StringIO()
        pivot_table(
            os.path.join(os.path.dirname(__file__), 'data/dates_pivoted.csv'),
            f_out,
            [0],
        )
        with data('dates_pivoted.converted.csv', 'r', newline='') as f_exp:
            self.assertEqual(
                f_out.getvalue(),
                f_exp.read(),
            )
