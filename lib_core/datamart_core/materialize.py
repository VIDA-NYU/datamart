import contextlib
import csv
import datamart_materialize
import logging
import os
import prometheus_client
import shutil
import zipfile

from datamart_core.common import hash_json
from datamart_core.fscache import cache_get_or_set
from datamart_materialize.excel import xls_to_csv
from datamart_materialize.pivot import pivot_table
from datamart_materialize.spss import spss_to_csv
from datamart_materialize.stata import stata_to_csv
from datamart_materialize.tsv import tsv_to_csv
from datamart_profiler import parse_date

from .discovery import encode_dataset_id
from .objectstore import get_object_store


logger = logging.getLogger(__name__)


PROM_DOWNLOAD = prometheus_client.Histogram(
    'download_seconds',
    "Time spent on download during materialization",
    buckets=[1.0, 10.0, 60.0, 120.0, 300.0, 600.0, 1800.0, 3600.0, 7200.0,
             float('inf')],
)
PROM_CONVERT = prometheus_client.Histogram(
    'convert_seconds',
    "Time spent on conversion during materialization",
    buckets=[1.0, 10.0, 60.0, 120.0, 300.0, 600.0, 1800.0, 3600.0, 7200.0,
             float('inf')],
)


def make_zip_recursive(zip_, src, dst=''):
    if os.path.isdir(src):
        for name in os.listdir(src):
            make_zip_recursive(
                zip_,
                os.path.join(src, name),
                dst + '/' + name if dst else name,
            )
    else:
        zip_.write(src, dst)


def dataset_cache_key(dataset_id, metadata, format, format_options):
    if format == 'csv':
        if format_options:
            raise ValueError
        materialize = metadata.get('materialize', {})
        metadata = {'id': dataset_id}
    else:
        metadata = dict(metadata, id=dataset_id)
        materialize = metadata.pop('materialize', {})
    h = hash_json({
        'format': format,
        'format_options': format_options,
        'metadata': metadata,
        'materialize': materialize,
        # Note that DATAMART_VERSION is NOT in here
        # We rely on the admin clearing the cache if required
    })
    # The hash is sufficient, other components are for convenience
    return '%s_%s.%s' % (
        encode_dataset_id(dataset_id),
        h,
        format,
    )


@contextlib.contextmanager
def get_dataset(metadata, dataset_id, format='csv', format_options=None):
    if not format:
        raise ValueError("Invalid output options")

    logger.info(
        "Getting dataset %r, size %s",
        dataset_id, metadata.get('size', 'unknown'),
    )

    object_store = get_object_store()

    # To limit the number of downloads, we always materialize the CSV file, and
    # convert it to the requested format if necessary. This avoids downloading
    # the CSV again just because we want a different format

    # Context to lock the CSV
    with contextlib.ExitStack() as dataset_lock:
        def create_csv(cache_temp):
            # Try to read from persistent storage
            with contextlib.ExitStack() as s3_stack:
                try:
                    csv_file = s3_stack.enter_context(
                        object_store.open('datasets', encode_dataset_id(dataset_id))
                    )
                except FileNotFoundError:
                    pass
                else:
                    logger.info("Reading from datasets bucket")
                    with open(cache_temp, 'wb') as fp:
                        shutil.copyfileobj(csv_file, fp)

                    return

            # Otherwise, materialize the CSV
            logger.info("Materializing CSV...")
            with PROM_DOWNLOAD.time():
                datamart_materialize.download(
                    {'id': dataset_id, 'metadata': metadata},
                    cache_temp, None,
                    format='csv',
                    size_limit=10000000000,  # 10 GB
                )

        csv_key = dataset_cache_key(dataset_id, metadata, 'csv', {})
        csv_path = dataset_lock.enter_context(
            cache_get_or_set(
                '/cache/datasets', csv_key, create_csv,
            )
        )

        # If CSV was requested, send it
        if format == 'csv':
            if format_options:
                raise ValueError("Invalid output options")
            yield csv_path
            return

        # Otherwise, do format conversion
        writer_cls = datamart_materialize.get_writer(format)
        if hasattr(writer_cls, 'parse_options'):
            format_options = writer_cls.parse_options(format_options)
        elif format_options:
            raise ValueError("Invalid output options")
        key = dataset_cache_key(
            dataset_id, metadata,
            format, format_options,
        )

        def create(cache_temp):
            # Do format conversion from CSV file
            logger.info("Converting CSV to %r opts=%r", format, format_options)
            with PROM_CONVERT.time():
                with open(csv_path, 'rb') as src:
                    writer = writer_cls(
                        cache_temp, format_options=format_options,
                    )
                    writer.set_metadata(dataset_id, metadata)
                    with writer.open_file('wb') as dst:
                        shutil.copyfileobj(src, dst)
                    writer.finish()

                # Make a ZIP if it's a folder
                if os.path.isdir(cache_temp):
                    logger.info("Result is a directory, creating ZIP file")
                    zip_name = cache_temp + '.zip'
                    with zipfile.ZipFile(zip_name, 'w') as zip_:
                        make_zip_recursive(zip_, cache_temp)
                    shutil.rmtree(cache_temp)
                    os.rename(zip_name, cache_temp)

        with dataset_lock.pop_all():
            cache_path = dataset_lock.enter_context(
                cache_get_or_set(
                    '/cache/datasets', key, create,
                )
            )
        yield cache_path


def detect_format_convert_to_csv(dataset_path, convert_dataset, materialize):
    """Detect supported formats and convert to CSV.

    :param dataset_path: Input dataset to be processed.
    :param convert_dataset: Function wrapping the conversion, in charge of
        creating the new file and cleaning up the previous one for each
        conversion. Takes the conversion function (filename, unicode file
        object), runs it, and returns the new path.
    :param materialize: Materialization info to be updated with the applied
        conversions.
    """
    with open(dataset_path, 'rb') as fp:
        magic = fp.read(16)

    # Check for Excel file format
    is_excel = False
    if magic[:4] == b'PK\x03\x04':  # 2007+
        try:
            zip = zipfile.ZipFile(dataset_path)
        except zipfile.BadZipFile:
            pass
        else:
            if any(info.filename.startswith('xl/') for info in zip.infolist()):
                is_excel = True
    elif magic[:8] == '\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1':  # 1997-2003
        is_excel = True
    if is_excel:
        # Update metadata
        logger.info("This is an Excel file")
        materialize.setdefault('convert', []).append({'identifier': 'xls'})

        # Update file
        dataset_path = convert_dataset(xls_to_csv, dataset_path)

    # Check for Stata file format
    if magic[:11] == b'<stata_dta>' or magic[:4] in (
        b'\x73\x01\x01\x00', b'\x73\x02\x01\x00',
        b'\x72\x01\x01\x00', b'\x72\x02\x01\x00',
        b'\x71\x01\x01\x01', b'\x71\x02\x01\x01',
    ):
        # Update metadata
        logger.info("This is a Stata file")
        materialize.setdefault('convert', []).append({'identifier': 'stata'})

        # Update file
        dataset_path = convert_dataset(stata_to_csv, dataset_path)

    # Check for SPSS file format
    if magic[:4] in (b'\xC1\xE2\xC3\xC9', b'$FL2', b'$FL3'):
        # Update metadata
        logger.info("This is an SPSS file")
        materialize.setdefault('convert', []).append({'identifier': 'spss'})

        # Update file
        dataset_path = convert_dataset(spss_to_csv, dataset_path)

    # Check for TSV file format
    with open(dataset_path, 'r') as fp:
        try:
            dialect = csv.Sniffer().sniff(fp.read(16384))
        except Exception as error:  # csv.Error, UnicodeDecodeError
            logger.warning("csv.Sniffer error: %s", error)
            dialect = csv.get_dialect('excel')
    if getattr(dialect, 'delimiter', ',') != ',':
        # Update metadata
        logger.info("Detected separator is %r", dialect.delimiter)
        materialize.setdefault('convert', []).append({
            'identifier': 'tsv',
            'separator': dialect.delimiter,
        })

        # Update file
        dataset_path = convert_dataset(
            lambda s, d: tsv_to_csv(s, d, separator=dialect.delimiter),
            dataset_path,
        )

    # Check for pivoted temporal table
    with open(dataset_path, 'r') as fp:
        reader = csv.reader(fp)
        try:
            columns = next(iter(reader))
        except StopIteration:
            columns = []
    if len(columns) >= 3:
        non_matches = [
            i for i, name in enumerate(columns)
            if parse_date(name) is None
        ]
        if len(non_matches) <= max(2.0, 0.20 * len(columns)):
            # Update metadata
            logger.info("Detected pivoted table")
            materialize.setdefault('convert', []).append({
                'identifier': 'pivot',
                'except_columns': non_matches,
            })

            # Update file
            dataset_path = convert_dataset(
                lambda path, dst: pivot_table(path, dst, non_matches),
                dataset_path,
            )

    return dataset_path
