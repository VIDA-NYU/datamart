import aio_pika
import asyncio
import elasticsearch
import functools
import hashlib
import html.entities
import json
import logging
import os
import re
import sentry_sdk
import sys
import threading

from . import types


logger = logging.getLogger(__name__)


class ThreadFormatter(logging.Formatter):
    """Variant of `Formatter` that shows the thread if it's not the main one.
    """
    _main_thread = threading.main_thread().ident

    def __init__(self, fmt=None, datefmt=None, threadedfmt=None):
        """Use ``%(threaded)s`` in ``fmt`` for the thread info, if not main.

        ``%(threaded)s`` expands to an empty string on the main thread, or to
        the thread ID otherwise.

        You can control the format of the ``threaded`` string by passing a
        `threadedfmt` argument, which defaults to ``' %(thread)d'``.

        Example usage::

            handler.formatter = ThreadFormatter(
                fmt="%(levelname)s %(name)s%(threaded)s: %(message)s",
                threadedfmt=" thread=%(thread)d",
            )
        """
        super(ThreadFormatter, self).__init__(fmt, datefmt)
        self._threadedfmt = threadedfmt or ' %(thread)d'

    def formatMessage(self, record):
        if record.thread != self._main_thread:
            record.threaded = self._threadedfmt % dict(
                thread=record.thread,
                threadname=record.threadName,
            )
        else:
            record.threaded = ''
        return super(ThreadFormatter, self).formatMessage(record)


def setup_logging(clear=True, thread=True):
    if clear:
        logging.root.handlers.clear()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if thread:
        logging.root.handlers[0].formatter = ThreadFormatter(
            "%(asctime)s %(levelname)s %(name)s%(threaded)s: %(message)s",
            threadedfmt=" thread=%(thread)d",
        )

    def filter_delete(record):
        if (
            len(record.args) >= 2 and
            record.args[0] == 'DELETE' and record.args[1].startswith('http')
        ):
            return False
        if (
            len(record.args) >= 3 and
            record.args[0] == 'GET' and record.args[1].startswith('http') and
            record.args[2] == 404
        ):
            return False
        return True

    logging.getLogger('elasticsearch').setLevel(logging.WARNING)
    logging.getLogger('elasticsearch').addFilter(filter_delete)

    # Enable Sentry
    if os.environ.get('SENTRY_DSN'):
        from sentry_sdk.integrations.tornado import TornadoIntegration
        logger.info("Initializing Sentry")
        sentry_sdk.init(
            dsn=os.environ['SENTRY_DSN'],
            integrations=[TornadoIntegration()],
            ignore_errors=[KeyboardInterrupt],
            release='auctus@%s' % os.environ['DATAMART_VERSION'],
        )


def block_wait_future(future):
    """Block the current thread until the future is done, return result.

    This is like ``await`` but for threads. Do not call this on the event-loop
    thread.
    """
    event = threading.Event()
    future.add_done_callback(lambda *a, **kw: event.set())
    event.wait()
    return future.result()


def block_run(loop, coro):
    """Block the current thread until the coroutine is done, return result.

    The coroutine should not have been submitted to asyncio yet. Do not call
    this on the event-loop thread.
    """
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return block_wait_future(future)


def json2msg(obj, **kwargs):
    return aio_pika.Message(json.dumps(obj).encode('utf-8'), **kwargs)


def msg2json(msg):
    return json.loads(msg.body.decode('utf-8'))


def log_future(future, logger, message="Exception in background task",
               should_never_exit=False):
    def log(future):
        try:
            future.result()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.exception(message)
        if should_never_exit:
            logger.critical("Critical task died, exiting")
            asyncio.get_event_loop().stop()
            sys.exit(1)
    future.add_done_callback(log)


re_non_path_safe = re.compile(r'[^A-Za-z0-9_.-]')


def encode_dataset_id(dataset_id):
    """Encode a dataset ID to a format suitable for file names.
    """
    dataset_id = dataset_id.replace('_', '__')
    dataset_id = re_non_path_safe.sub(lambda m: '_%X' % ord(m.group(0)),
                                      dataset_id)
    return dataset_id


def decode_dataset_id(dataset_id):
    """Decode a dataset ID encoded using `encode_dataset_id()`.
    """
    dataset_id = list(dataset_id)
    i = 0
    while i < len(dataset_id):
        if dataset_id[i] == '_':
            if dataset_id[i + 1] == '_':
                del dataset_id[i + 1]
            else:
                char_hex = dataset_id[i + 1:i + 3]
                dataset_id[i + 1:i + 3] = []
                char_hex = ''.join(char_hex)
                dataset_id[i] = chr(int(char_hex, 16))
        i += 1
    return ''.join(dataset_id)


def hash_json(*args, **kwargs):
    if not args:
        dct = dict()
    elif len(args) == 1:
        dct = args[0]
        assert isinstance(dct, dict)
    else:
        raise TypeError("Expected 1 positional argument, got %d" % len(args))

    dct.update(**kwargs)

    bytes_ = json.dumps(dct, sort_keys=True).encode('utf-8')
    return hashlib.sha1(bytes_).hexdigest()


_re_html_link = re.compile(
    r'<a [^>]*\bhref="(https?://[^"]+)"[^>]*>(.*?)</a>',
)
_re_html_tag = re.compile(
    r'</?(?:a|acronym|br|div|em|h[1-5]|li|ol|p|span|ul)(?: [^>]*)?/?>',
)
_re_html_entities = re.compile(
    r'&([A-Za-z]{2,35};)',
)


def _base_url(url):
    if url.startswith('http://'):
        url = url[7:]
    elif url.startswith('https://'):
        url = url[8:]
    else:
        url = url
    return url.rstrip('/')


def strip_html(text):
    # Replace links
    def replace_link(m):
        url, label = m.groups()
        if _base_url(url) == _base_url(label):
            return label
        else:
            return "%s (%s)" % (label, url)
    text = _re_html_link.sub(replace_link, text)

    # Strip tags
    text = _re_html_tag.sub('', text)

    # Fix entities
    text = _re_html_entities.sub(
        lambda x: html.entities.html5.get(x.group(1), x.group(0)),
        text,
    )

    return text


def contextdecorator(factory, argname):
    def inner(wrapped):
        @functools.wraps(wrapped)
        def wrapper(*args, **kwargs):
            with factory() as ctx:
                kwargs.update({argname: ctx})
                return wrapped(*args, **kwargs)
        return wrapper
    return inner


def add_dataset_to_sup_index(es, dataset_id, metadata):
    """
    Adds dataset to the supplementary Datamart indices: 'datamart_columns',
    'datamart_spatial_coverage', and 'datamart_temporal_coverage'.
    """
    DISCARD_DATASET_FIELDS = [
        'columns', 'sample', 'materialize',
        'spatial_coverage', 'temporal_coverage',
        'manual_annotations',
    ]
    DISCARD_COLUMN_FIELDS = ['plot']

    common_dataset_metadata = dict(dataset_id=dataset_id)
    for key, value in metadata.items():
        if key not in DISCARD_DATASET_FIELDS:
            common_dataset_metadata['dataset_' + key] = value

    # 'datamart_columns' index
    for column_index, column in enumerate(metadata['columns']):
        column_metadata = dict(column)
        for field in DISCARD_COLUMN_FIELDS:
            column_metadata.pop(field, None)
        column_metadata.update(common_dataset_metadata)
        column_metadata['index'] = column_index
        if 'coverage' in column_metadata:
            column_metadata['coverage'] = [
                dict(
                    num_range,
                    gte=num_range['range']['gte'],
                    lte=num_range['range']['lte'],
                )
                for num_range in column_metadata['coverage']
            ]
        es.index(
            'datamart_columns',
            column_metadata
        )

    # 'datamart_spatial_coverage' index
    if 'spatial_coverage' in metadata:
        for spatial_coverage in metadata['spatial_coverage']:
            spatial_coverage_metadata = dict()
            spatial_coverage_metadata.update(common_dataset_metadata)
            spatial_coverage_metadata.update(spatial_coverage)
            ranges = []
            # Keep in sync, search code for 279a32
            for spatial_range in spatial_coverage_metadata['ranges']:
                coordinates = spatial_range['range']['coordinates']
                ranges.append(dict(
                    spatial_range,
                    min_lon=coordinates[0][0],
                    max_lat=coordinates[0][1],
                    max_lon=coordinates[1][0],
                    min_lat=coordinates[1][1],
                ))
            spatial_coverage_metadata['ranges'] = ranges
            es.index(
                'datamart_spatial_coverage',
                spatial_coverage_metadata,
            )

    # 'datamart_temporal_coverage' index
    if 'temporal_coverage' in metadata:
        for temporal_coverage in metadata['temporal_coverage']:
            temporal_coverage_metadata = dict()
            temporal_coverage_metadata.update(common_dataset_metadata)
            temporal_coverage_metadata.update(temporal_coverage)
            temporal_coverage_metadata['ranges'] = [
                dict(
                    temporal_range,
                    gte=temporal_range['range']['gte'],
                    lte=temporal_range['range']['lte'],
                )
                for temporal_range in temporal_coverage_metadata['ranges']
            ]
            es.index(
                'datamart_temporal_coverage',
                temporal_coverage_metadata,
            )


def add_dataset_to_index(es, dataset_id, metadata):
    """
    Safely adds a dataset to all the Datamart indices.
    """

    # 'datamart' index
    es.index(
        'datamart',
        dict(metadata, id=dataset_id),
        id=dataset_id,
    )

    add_dataset_to_sup_index(
        es,
        dataset_id,
        metadata
    )


def add_dataset_to_lazo_storage(es, id, metadata):
    """Adds a dataset to Lazo.
    """

    es.index(
        'lazo',
        metadata,
        id=id,
    )


def delete_dataset_from_lazo(es, dataset_id, lazo_client):
    query = {
        'query': {
            'bool': {
                'must': [
                    {'term': {'dataset_id': dataset_id}},
                    {'term': {'structural_type': types.TEXT}}
                ],
                'must_not': {
                    'term': {'semantic_types': types.DATE_TIME}
                }
            }
        }
    }
    textual_columns = list()
    # FIXME: Use search-after API here?
    from_ = 0
    while True:
        hits = es.search(
            index='datamart_columns',
            body=query,
            from_=from_,
            size=10000,
        )['hits']['hits']
        from_ += len(hits)
        for h in hits:
            textual_columns.append(h['_source']['name'])
        if len(hits) != 10000:
            break

    if textual_columns:
        ack = lazo_client.remove_sketches(dataset_id, textual_columns)
        if ack:
            logger.info(
                "Deleted %d documents from Lazo",
                len(textual_columns)
            )
        else:
            logger.info("Error while deleting documents from Lazo")


def delete_dataset_from_index(es, dataset_id, lazo_client=None):
    """
    Safely deletes a dataset from the 'datamart' index,
    including its corresponding information in 'datamart_columns',
    'datamart_spatial_coverage', and 'datamart_temporal_coverage' indices.
    This function also connects to the Lazo index service
    and deletes any corresponding sketch.
    """

    if lazo_client:
        # checking if there are any textual columns in the dataset
        # remove them from the Lazo index service
        delete_dataset_from_lazo(es, dataset_id, lazo_client)

    # Remove from alternate index
    try:
        es.delete('pending', dataset_id)
    except elasticsearch.NotFoundError:
        pass

    # deleting from 'datamart'
    try:
        es.delete('datamart', dataset_id)
    except elasticsearch.NotFoundError:
        return

    # deleting from 'datamart_columns', 'datamart_spatial_coverage', and
    # 'datamart_temporal_coverage'
    query = {
        'query': {
            'term': {'dataset_id': dataset_id}
        }
    }
    for index in (
        'datamart_columns',
        'datamart_spatial_coverage',
        'datamart_temporal_coverage',
    ):
        nb = es.delete_by_query(
            index=index,
            body=query,
        )['deleted']
        logger.info("Deleted %d documents from %s", nb, index)
