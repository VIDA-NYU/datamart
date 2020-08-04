import codecs
import io
import logging
from pkg_resources import iter_entry_points
import requests
import threading
import time


__version__ = '0.7'


logger = logging.getLogger(__name__)


class UnconfiguredMaterializer(Exception):
    """Raised by a materializer when it is not configured.

    Some materializers need configuration, such as an API key or token.
    """


class DatasetTooBig(Exception):
    """Raised when the size limit is reached during materialization.

    Some materializers can't tell the size of a dataset before they download
    it, so the only solution is to raise this error when it is reached.
    """


def _write_file(response, writer, size_limit=None):
    """Write download results to disk.
    """
    size = 0
    with writer.open_file('wb') as fp:
        for chunk in response.iter_content(chunk_size=4096):
            if chunk:  # filter out keep-alive chunks
                fp.write(chunk)
                size += len(chunk)
                if size_limit is not None and size > size_limit:
                    raise DatasetTooBig


def _direct_download(url, writer, size_limit=None):
    """Direct download of a file from a URL.

    This is used when the materialization info contains a ``direct_url`` key,
    to simply download the file from the source.

    :param url: URL of the file to download.
    :param writer: Output writer used to write the dataset.
    """
    response = requests.get(url, allow_redirects=True, stream=True)
    response.raise_for_status()
    _write_file(response, writer, size_limit=size_limit)


def _proxy_download(dataset_id, writer, proxy, size_limit=None):
    """Use a Datamart service to materialize for us.

    This is used when the materializer is not available locally. We request
    that the Datamart handle materialization, and then we download from there.

    :param dataset_id: Dataset ID from search index.
    :param writer: Output writer used to write the dataset.
    :param proxy: URL of a Datamart server to use as a proxy.
    """
    response = requests.get(proxy + '/download/' + dataset_id,
                            allow_redirects=True, stream=True)
    response.raise_for_status()
    if (
        size_limit is not None and
        'Content-Length' in response.headers and
        int(response.headers['Content-Length']) > size_limit
    ):
        raise DatasetTooBig
    _write_file(response, writer, size_limit=size_limit)


materializers = {}
writers = {}
converters = {}

_materializers_loaded = False

_materializers_mutex = threading.Lock()


def load_materializers():
    """Load materializers/writers from package entrypoint metadata.

    This is called automatically the first time we need it, or you can call it
    again if more materializers/writers get installed.
    """
    global materializers, writers, converters, _materializers_loaded
    with _materializers_mutex:
        if _materializers_loaded:
            return

        def load(what, entry_point_name):
            result = {}
            for entry_point in iter_entry_points(entry_point_name):
                try:
                    obj = entry_point.load()
                except Exception:
                    logger.exception("Failed to load %s %s from %s %s",
                                     what,
                                     entry_point.name,
                                     entry_point.dist.project_name,
                                     entry_point.dist.version)
                else:
                    result[entry_point.name] = obj
                    logger.info("%s loaded: %s", what, entry_point.name)
            return result

        materializers = load('materializer', 'datamart_materialize')
        writers = load('writer', 'datamart_materialize.writer')
        converters = load('converter', 'datamart_materialize.converter')

        _materializers_loaded = True


def get_writer(format):
    if not _materializers_loaded:
        load_materializers()

    try:
        return writers[format]
    except KeyError:
        raise ValueError("No writer for output format %r" % format)


class CsvWriter(object):
    """Writer for the ``csv`` format. Writes a CSV file at the provided path.
    """
    needs_metadata = False
    extension = '.csv'

    def __init__(self, destination, format_options=None):
        self.destination = destination

    def open_file(self, mode='wb', name=None):
        if name is not None:
            raise ValueError("CsvWriter can only write single-table datasets")
        if hasattr(self.destination, 'write'):
            fileobj = self.destination
        elif 'b' in mode:
            return open(self.destination, mode)
        else:
            return open(self.destination, mode, encoding='utf-8')
        if mode == 'wb':
            return fileobj
        else:
            return codecs.getwriter('utf-8')(fileobj)

    def set_metadata(self, dataset_id, metadata):
        pass

    def finish(self):
        return None


class PandasWriter(object):
    """Writer for the ``pandas`` format. Buffers a CSV file in memory, and
    returns a :class:`pandas.DataFrame` object at the end.
    """
    needs_metadata = False

    class _PandasFile(object):
        def __init__(self, mode='wb'):
            if mode == 'wb':
                self._data = io.BytesIO()
            elif mode == 'w':
                self._data = io.StringIO()
            else:
                raise ValueError("Invalid mode %r", mode)

        def close(self):
            pass  # DON'T close the underlying file, we'll need to read it

        def write(self, buffer):
            return self._data.write(buffer)

        def flush(self):
            self._data.flush()

        def __enter__(self):
            self._data.__enter__()
            return self

        def __exit__(self, exc, value, tb):
            pass

    def __init__(self, destination, format_options=None):
        if destination is not None:
            raise ValueError("Pandas format expects destination=None")
        self._data = None

    def open_file(self, mode='wb', name=None):
        if name is not None:
            raise ValueError(
                "PandasWriter can only write single-table datasets"
            )
        self._data = self._PandasFile(mode)
        return self._data

    def set_metadata(self, dataset_id, metadata):
        pass

    def finish(self):
        import pandas

        data = self._data._data  # unwrap the underlying BytesIO/StringIO

        # Feed it to pandas
        data.seek(0, 0)
        return pandas.read_csv(data)


def make_writer(destination, format='csv', format_options=None):
    writer_cls = get_writer(format)
    return writer_cls(destination, format_options=format_options)


def download(dataset, destination, proxy, format='csv', format_options=None,
             size_limit=None):
    """Materialize a dataset on disk.

    :param dataset: Dataset description from search index.
    :param destination: Path where the dataset will be written.
    :param proxy: URL of a Datamart server to use as a proxy if we can't
        materialize locally. If ``None``, :class:`KeyError` will be raised if this
        materializer is unavailable.
    :param format: Output format.
    :param format_options: Dictionary of options for the writer or None.
    :param size_limit: Maximum size of the dataset to download, in bytes. If
        the limit is reached, :class:`DatasetTooBig` will be raised.
    """
    writer = make_writer(destination, format, format_options)

    if isinstance(dataset, str):
        # If dataset is just an ID, we use the proxy
        if proxy:
            metadata = None
            if getattr(writer, 'needs_metadata', False):
                logger.info("Obtaining metadata from proxy...")
                response = requests.get(proxy + '/metadata/' + dataset)
                response.raise_for_status()
                metadata = response.json()['metadata']
            if metadata is not None:
                writer.set_metadata(dataset, metadata)
            _proxy_download(dataset, writer, proxy, size_limit=size_limit)
            return writer.finish()
        else:
            raise ValueError("A proxy must be specified to download a dataset "
                             "from its ID")
    elif not isinstance(dataset, dict):
        raise TypeError("'dataset' must be either a str or a dict")
    else:
        dataset_id = None
        materialize = dataset
        metadata = None
        if 'metadata' in materialize:
            metadata = materialize = materialize['metadata']
            dataset_id = dataset.get('id')
        if 'materialize' in materialize:
            metadata = materialize
            materialize = materialize['materialize']
            dataset_id = dataset.get('id')

    if metadata is not None:
        writer.set_metadata(dataset_id, metadata)

    for converter in reversed(materialize.get('convert', [])):
        converter_args = dict(converter)
        converter_id = converter_args.pop('identifier')
        converter_class = converters[converter_id]
        writer = converter_class(writer, **converter_args)

    if 'direct_url' in materialize:
        logger.info("Direct download: %s", materialize['direct_url'])
        start = time.perf_counter()
        _direct_download(
            materialize['direct_url'], writer,
            size_limit=size_limit,
        )
        logger.info("Download successful, %.2fs", time.perf_counter() - start)
        return writer.finish()
    elif 'identifier' in materialize:
        identifier = materialize['identifier']
        try:
            materializer = materializers[identifier]
        except KeyError:
            pass
        else:
            try:
                logger.info("Calling materializer...")
                start = time.perf_counter()
                materializer.download(
                    materialize, writer,
                    size_limit=size_limit,
                )
                logger.info("Materializer successful, %.2fs",
                            time.perf_counter() - start)
                return writer.finish()
            except UnconfiguredMaterializer as e:
                logger.warning("Materializer is not configured properly: %s",
                               ", ".join(e.args))
        if proxy and dataset_id:
            logger.info("Calling materialization proxy...")
            start = time.perf_counter()
            _proxy_download(dataset_id, writer, proxy, size_limit=size_limit)
            logger.info("Materialization through proxy successful, %.2fs",
                        time.perf_counter() - start)
            return writer.finish()
        else:
            raise KeyError("Materializer unavailable: '%s'" % identifier)
    else:
        raise ValueError("Invalid materialization info")

    # unreachable
