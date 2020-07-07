import codecs
import csv
import io
import json
import logging
import os

from . import types


logger = logging.getLogger(__name__)


STRUCTURAL_TYPE_MAP = {
    types.BOOLEAN: 'boolean',
    types.INTEGER: 'integer',
    types.FLOAT: 'real',
    types.TEXT: 'string',
}


DEFAULT_VERSION = '4.0.0'


def d3m_metadata(dataset_id, metadata, *, version=None, need_d3mindex=False):
    if not version:
        version = DEFAULT_VERSION
    elif version not in ('3.2.0', '4.0.0'):
        raise ValueError("Unknown D3M schema version %r" % (version,))

    columns = metadata['columns']

    if (
        need_d3mindex and
        not any(c['name'] == 'd3mIndex' for c in columns)
    ):
        d3mindex_meta = {
            'name': 'd3mIndex',
            'structural_type': types.INTEGER,
            'semantic_types': [types.ID],
        }
        columns = [d3mindex_meta] + columns

    d3m_columns = []
    for i, column in enumerate(columns):
        if types.BOOLEAN in column['semantic_types']:
            col_type = 'boolean'
        elif types.CATEGORICAL in column['semantic_types']:
            col_type = 'categorical'
        elif (
            column['structural_type'] == types.TEXT
            and types.DATE_TIME in column['semantic_types']
        ):
            col_type = 'dateTime'
        else:
            col_type = STRUCTURAL_TYPE_MAP.get(
                column['structural_type'],
                'string',
            )
        role = 'index' if column['name'] == 'd3mIndex' else 'attribute'
        d3m_columns.append({
            'colIndex': i,
            'colName': column['name'],
            'colType': col_type,
            'role': [role],
        })

    d3m_meta = {
        'about': {
            'datasetID': dataset_id,
            'datasetName': metadata.get('name', dataset_id),
            'license': metadata.get('license', 'unknown'),
            'datasetSchemaVersion': version,
            'redacted': False,
            'datasetVersion': '1.0',
        },
        'dataResources': [
            {
                'resID': 'learningData',
                'resPath': 'tables/learningData.csv',
                'resType': 'table',
                'resFormat': (
                    {'text/csv': ["csv"]} if version == '4.0.0'
                    else ['text/csv']
                ),
                'isCollection': False,
                'columns': d3m_columns,
            },
        ],
    }
    if 'description' in metadata:
        d3m_meta['about']['description'] = metadata['description']
    if 'size' in metadata:
        d3m_meta['about']['approximateSize'] = '%d B' % metadata['size']
    if 'qualities' in metadata:
        d3m_meta['qualities'] = metadata.get('qualities')

    return d3m_meta


class _D3mAddIndex(object):
    BUFFER_MAX = 102400  # 100 kiB

    def __init__(self, dest_fp, binary):
        self._buffer = io.StringIO()
        self._generate = None
        self._dest_fp = dest_fp
        self._dest_csv = None
        self._idx = -1
        if binary:
            self._decoder = codecs.getincrementaldecoder('utf-8')()
        else:
            self._decoder = None

    def __iter__(self):
        # Pandas needs file objects to have __iter__
        return self

    def _peek_line(self):
        # Make a CSV reader for the buffer
        self._buffer.seek(0, 0)
        reader = iter(csv.reader(self._buffer))

        # Try to read two lines, which means we have one complete one
        try:
            line = next(reader)
            next(reader)
        except StopIteration:
            line = None

        # Reset buffer
        self._buffer.seek(0, 2)

        return line

    def _get_lines(self):
        # Make a CSV reader for the buffer
        self._buffer.seek(0, 0)
        reader = iter(csv.reader(self._buffer))

        # Read lines, making sure they're complete by reading the following one
        try:
            prev = next(reader)
        except StopIteration:
            return
        prevpos = 0
        pos = self._buffer.tell()
        while prev:
            try:
                line = next(reader)
            except StopIteration:
                break
            yield prev
            prev = line
            prevpos = pos
            pos = self._buffer.tell()

        # Remove what was read from the buffer
        self._buffer = io.StringIO(self._buffer.getvalue()[prevpos:])
        self._buffer.seek(0, 2)

    def write(self, buf):
        if self._decoder is not None:
            buf = self._decoder.decode(buf)
        if self._generate is False:
            return self._dest_fp.write(buf)
        self._buffer.write(buf)
        if self._buffer.tell() > self.BUFFER_MAX:
            self._flush()
        return len(buf)

    def _flush(self):
        if self._generate is None:
            # Decide whether the index needs to be generated
            columns = self._peek_line()
            if columns is None:
                raise ValueError("Couldn't read CSV header")
            if 'd3mIndex' in columns:
                self._generate = False
                self._dest_fp.write(self._buffer.getvalue())
                self._buffer = None
                return
            else:
                logger.info("No 'd3mIndex' column, generating one")
                self._generate = True
                self._dest_csv = csv.writer(self._dest_fp)
        for line in self._get_lines():
            if self._idx == -1:
                self._dest_csv.writerow(['d3mIndex'] + line)
            else:
                self._dest_csv.writerow([self._idx] + line)
            self._idx += 1

    def close(self):
        if self._generate is not False:
            if self._decoder is not None:
                # Flush decoder
                self._buffer.write(self._decoder.decode(b'', True))
            self._flush()
            if self._generate is True:
                # Write last line
                self._buffer.seek(0, 0)
                try:
                    line = next(iter(csv.reader(self._buffer)))
                except StopIteration:
                    line = ''
                if line:
                    self._dest_csv.writerow([self._idx] + line)
        self._dest_fp.close()
        self._dest_csv = None
        self._dest_fp = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class D3mWriter(object):
    """Writer for the ``d3m`` dataset format, following MIT-LL's schema.

    https://gitlab.com/datadrivendiscovery/data-supply

    The key ``version`` can be passed in `format_options` to select the version
    of the schema to generate, between ``3.2.0`` and ``4.0.0``.
    """
    needs_metadata = True
    default_options = {'version': DEFAULT_VERSION, 'need_d3mindex': False}

    @classmethod
    def parse_options(cls, options):
        merged_options = dict(cls.default_options)
        if options:
            merged_options.update(options)
        unknown_keys = merged_options.keys() - cls.default_options
        if unknown_keys:
            raise ValueError(
                "Invalid format option %r" % (next(iter(unknown_keys)),)
            )
        merged_options['need_d3mindex'] = (
            merged_options['need_d3mindex'] not in (
                False, '', 'False', 'false', '0', 'off', 'no',
            )
        )
        return merged_options

    def __init__(self, destination, format_options=None):
        format_options = self.parse_options(format_options)
        self.version = format_options['version']
        self.need_d3mindex = format_options['need_d3mindex']

        self.destination = destination
        os.mkdir(destination)
        os.mkdir(os.path.join(destination, 'tables'))

    def open_file(self, mode='wb', name=None):
        if name is not None:
            raise ValueError("D3mWriter can only write single-table datasets "
                             "for now")
        if self.need_d3mindex:
            fp = open(
                os.path.join(self.destination, 'tables', 'learningData.csv'),
                'w',
                encoding='utf-8',
                newline='',
            )
            fp = _D3mAddIndex(fp, 'b' in mode)
            return fp
        else:
            return open(
                os.path.join(self.destination, 'tables', 'learningData.csv'),
                mode,
            )

    def set_metadata(self, dataset_id, metadata):
        d3m_meta = d3m_metadata(
            dataset_id, metadata,
            version=self.version, need_d3mindex=self.need_d3mindex,
        )

        json_path = os.path.join(self.destination, 'datasetDoc.json')
        with open(json_path, 'w', encoding='utf-8', newline='') as fp:
            json.dump(d3m_meta, fp, sort_keys=True, indent=2)

    def finish(self):
        return None
