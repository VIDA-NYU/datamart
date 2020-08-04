import aio_pika
import asyncio
import contextlib
from datetime import datetime
import defusedxml
import elasticsearch
import itertools
import lazo_index_service
import logging
import os
import prometheus_client
import threading
import time

from datamart_core.common import setup_logging, add_dataset_to_index, \
    delete_dataset_from_index, delete_dataset_from_lazo, log_future, \
    json2msg, msg2json
from datamart_core.fscache import cache_get_or_set
from datamart_core.materialize import get_dataset, dataset_cache_key, \
    detect_format_convert_to_csv
from datamart_geo import GeoData
from datamart_materialize import DatasetTooBig
from datamart_profiler import process_dataset


logger = logging.getLogger(__name__)


MAX_CONCURRENT_PROFILE = 1
MAX_CONCURRENT_DOWNLOAD = 2


PROM_DOWNLOADING = prometheus_client.Gauge(
    'profile_downloading_count', "Number of datasets currently downloading",
)
PROM_PROFILING = prometheus_client.Gauge(
    'profile_profiling_count', "Number of datasets currently profiling",
)


# https://xlrd.readthedocs.io/en/latest/vulnerabilities.html
defusedxml.defuse_stdlib()


@contextlib.contextmanager
def prom_incremented(metric, amount=1):
    """Context manager that increments a metric, then decrements it at the end.
    """
    metric.inc(amount)
    try:
        yield
    finally:
        metric.dec(amount)


# FIXME: Work around https://gitlab.com/ViDA-NYU/datamart/datamart/-/issues/47
class LazoDeleteFirst(object):
    def __init__(self, lazo_client, es, dataset_id):
        self._deleted = False
        self._lazo = lazo_client
        self._es = es
        self._dataset_id = dataset_id

    def _delete(self):
        if not self._deleted:
            self._deleted = True
            delete_dataset_from_lazo(self._es, self._dataset_id, self._lazo)

    def index_data_path(self, *args, **kwargs):
        self._delete()
        return self._lazo.index_data_path(*args, **kwargs)

    def index_data(self, *args, **kwargs):
        self._delete()
        return self._lazo.index_data(*args, **kwargs)

    def get_lazo_sketch_from_data_path(self, *args, **kwargs):
        return self._lazo.get_lazo_sketch_from_data_path(*args, **kwargs)

    def get_lazo_sketch_from_data(self, *args, **kwargs):
        return self._lazo.get_lazo_sketch_from_data(*args, **kwargs)


def materialize_and_process_dataset(
    dataset_id, metadata,
    lazo_client, nominatim, geo_data,
    profile_semaphore,
):
    with contextlib.ExitStack() as stack:
        # Remove converters, we'll discover what's needed
        metadata = dict(metadata)
        materialize = dict(metadata.pop('materialize'))
        materialize.pop('convert', None)

        with prom_incremented(PROM_DOWNLOADING):
            dataset_path = stack.enter_context(
                get_dataset(
                    dict(metadata, materialize=materialize),
                    dataset_id,
                )
            )

        def convert_dataset(func, path):
            def convert(cache_temp):
                with open(cache_temp, 'w', newline='') as dst:
                    func(path, dst)
            converted_key = dataset_cache_key(
                dataset_id,
                dict(metadata, materialize=materialize),
                'csv',
                {},
            )
            return stack.enter_context(
                cache_get_or_set(
                    '/cache/datasets',
                    converted_key,
                    convert,
                )
            )

        dataset_path = detect_format_convert_to_csv(
            dataset_path,
            convert_dataset,
            materialize,
        )

        # Profile
        with profile_semaphore:
            with prom_incremented(PROM_PROFILING):
                logger.info("Profiling dataset %r", dataset_id)
                start = time.perf_counter()
                metadata = process_dataset(
                    data=dataset_path,
                    dataset_id=dataset_id,
                    metadata=metadata,
                    lazo_client=lazo_client,
                    nominatim=nominatim,
                    geo_data=geo_data,
                    include_sample=True,
                    coverage=True,
                    plots=True,
                )
                logger.info(
                    "Profiling dataset %r took %.2fs",
                    dataset_id,
                    time.perf_counter() - start,
                )

        metadata['materialize'] = materialize
        return metadata


class Profiler(object):
    def __init__(self):
        self.profile_semaphore = threading.Semaphore(MAX_CONCURRENT_PROFILE)
        self.es = elasticsearch.Elasticsearch(
            os.environ['ELASTICSEARCH_HOSTS'].split(',')
        )
        self.lazo_client = lazo_index_service.LazoIndexClient(
            host=os.environ['LAZO_SERVER_HOST'],
            port=int(os.environ['LAZO_SERVER_PORT'])
        )
        if os.environ.get('NOMINATIM_URL'):
            self.nominatim = os.environ['NOMINATIM_URL']
        else:
            self.nominatim = None
            logger.warning(
                "$NOMINATIM_URL is not set, not resolving URLs"
            )
        self.geo_data = GeoData.from_local_cache()
        self.channel = None

        self.geo_data.load_areas([0, 1, 2], bounds=True)

        self.loop = asyncio.get_event_loop()
        log_future(self.loop.create_task(self._run()), logger,
                   should_never_exit=True)

        # Retry a few times, in case the Elasticsearch container is not yet up
        for i in itertools.count():
            try:
                if not self.es.indices.exists('datamart'):
                    raise RuntimeError("'datamart' index does not exist")
            except Exception:
                logger.warning("Can't connect to Elasticsearch, retrying...")
                if i == 5:
                    raise
                else:
                    time.sleep(5)
            else:
                break

    async def _amqp_setup(self):
        # Setup the datasets exchange
        self.datasets_exchange = await self.channel.declare_exchange(
            'datasets',
            aio_pika.ExchangeType.TOPIC)

        # Setup the profiling exchange
        self.profile_exchange = await self.channel.declare_exchange(
            'profile',
            aio_pika.ExchangeType.FANOUT,
        )

        # Declare the profiling queue
        self.profile_queue = await self.channel.declare_queue(
            'profile',
            arguments={'x-max-priority': 3},
        )
        await self.profile_queue.bind(self.profile_exchange)

        # Declare the failed queue
        self.failed_queue = await self.channel.declare_queue('failed_profile')

    async def _run(self):
        connection = await aio_pika.connect_robust(
            host=os.environ['AMQP_HOST'],
            port=int(os.environ['AMQP_PORT']),
            login=os.environ['AMQP_USER'],
            password=os.environ['AMQP_PASSWORD'],
        )
        self.channel = await connection.channel()
        await self.channel.set_qos(prefetch_count=MAX_CONCURRENT_DOWNLOAD)

        await self._amqp_setup()

        # Consume profiling queue
        async for message in self.profile_queue:
            obj = msg2json(message)
            dataset_id = obj['id']
            metadata = obj['metadata']
            materialize = metadata.get('materialize', {})

            logger.info("Processing dataset %r from %r",
                        dataset_id, materialize.get('identifier'))

            future = self.loop.run_in_executor(
                None,
                materialize_and_process_dataset,
                dataset_id,
                metadata,
                LazoDeleteFirst(self.lazo_client, self.es, dataset_id),
                self.nominatim,
                self.geo_data,
                self.profile_semaphore,
            )

            future.add_done_callback(
                self.process_dataset_callback(
                    message, dataset_id,
                )
            )

    def process_dataset_callback(self, message, dataset_id):
        async def coro(future):
            metadata = msg2json(message)['metadata']
            try:
                try:
                    metadata = future.result()
                    if metadata['nb_rows'] == 0:
                        logger.info(
                            "Dataset has no rows, not inserting into index: " +
                            "%r",
                            dataset_id,
                        )
                        delete_dataset_from_index(
                            self.es,
                            dataset_id,
                            # DO delete from Lazo
                            self.lazo_client,
                        )
                        self.es.index(
                            'pending',
                            dict(
                                status='error',
                                error="Dataset has no rows",
                                metadata=metadata,
                                date=datetime.utcnow().isoformat(),
                                source=metadata['source'],
                                materialize=metadata['materialize'],
                            ),
                            id=dataset_id,
                        )
                    else:
                        # Delete dataset if already exists in index
                        delete_dataset_from_index(
                            self.es,
                            dataset_id,
                            # Don't delete from Lazo, we inserted during profile
                            None,
                        )
                        # Insert results in Elasticsearch
                        body = dict(metadata,
                                    date=datetime.utcnow().isoformat() + 'Z',
                                    version=os.environ['DATAMART_VERSION'])
                        add_dataset_to_index(self.es, dataset_id, body)

                        # Publish to RabbitMQ
                        await self.datasets_exchange.publish(
                            json2msg(dict(body, id=dataset_id)),
                            dataset_id,
                        )

                        # Remove from alternate index
                        try:
                            self.es.delete('pending', dataset_id)
                        except elasticsearch.NotFoundError:
                            pass
                except DatasetTooBig:
                    # Materializer reached size limit
                    logger.info("Dataset over size limit: %r", dataset_id)
                    message.ack()
                    self.es.index(
                        'pending',
                        dict(
                            status='error',
                            error="Dataset is too big",
                            metadata=metadata,
                            date=datetime.utcnow().isoformat(),
                            source=metadata['source'],
                            materialize=metadata['materialize'],
                        ),
                        id=dataset_id,
                    )
                    try:
                        self.es.delete(
                            'datamart',
                            dataset_id,
                        )
                    except elasticsearch.NotFoundError:
                        pass
                except Exception as e:
                    if isinstance(e, elasticsearch.RequestError):
                        # This is a problem with our computed metadata
                        logger.exception(
                            "Error inserting dataset %r in Elasticsearch",
                            dataset_id,
                        )
                    elif isinstance(e, elasticsearch.TransportError):
                        # This is probably an issue with Elasticsearch
                        # We'll log, nack and retry
                        raise
                    else:
                        logger.exception("Error processing dataset %r",
                                         dataset_id)
                    # Move message to failed queue
                    await self.channel.default_exchange.publish(
                        aio_pika.Message(message.body),
                        self.failed_queue.name,
                    )
                    # Ack anyway, retrying would probably fail again
                    message.ack()

                    self.es.index(
                        'pending',
                        dict(
                            status='error',
                            error="Error profiling dataset",
                            metadata=metadata,
                            date=datetime.utcnow().isoformat(),
                            source=metadata['source'],
                            materialize=metadata['materialize'],
                        ),
                        id=dataset_id,
                    )
                else:
                    message.ack()
                    logger.info("Dataset %r processed successfully",
                                dataset_id)
            except Exception:
                message.nack()
                raise

        def callback(future):
            log_future(self.loop.create_task(coro(future)), logger)

        return callback


def main():
    setup_logging()
    prometheus_client.start_http_server(8000)
    logger.info("Startup: profiler %s", os.environ['DATAMART_VERSION'])
    Profiler()
    asyncio.get_event_loop().run_forever()


if __name__ == '__main__':
    main()
