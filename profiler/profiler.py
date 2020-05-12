import aio_pika
import asyncio
import contextlib
import csv
from datetime import datetime
import elasticsearch
import itertools
import lazo_index_service
import logging
import os
import prometheus_client
import threading
import time
import xlrd

from datamart_core.common import setup_logging, add_dataset_to_index, \
    delete_dataset_from_index, log_future, json2msg, msg2json
from datamart_core.materialize import get_dataset
from datamart_materialize import DatasetTooBig
from datamart_materialize.excel import xls_to_csv
from datamart_materialize.pivot import pivot_table
from datamart_materialize.tsv import tsv_to_csv
from datamart_profiler import process_dataset, parse_date


logger = logging.getLogger(__name__)


MAX_CONCURRENT_PROFILE = 1
MAX_CONCURRENT_DOWNLOAD = 2


PROM_DOWNLOADING = prometheus_client.Gauge(
    'profile_downloading_count', "Number of datasets currently downloading",
)
PROM_PROFILING = prometheus_client.Gauge(
    'profile_profiling_count', "Number of datasets currently profiling",
)


@contextlib.contextmanager
def prom_incremented(metric, amount=1):
    """Context manager that increments a metric, then decrements it at the end.
    """
    metric.inc(amount)
    try:
        yield
    finally:
        metric.dec(amount)


def materialize_and_process_dataset(
    dataset_id, metadata,
    lazo_client, nominatim,
    profile_semaphore,
    cache_invalid=False,
):
    with contextlib.ExitStack() as stack:
        with prom_incremented(PROM_DOWNLOADING):
            dataset_path = stack.enter_context(
                get_dataset(metadata, dataset_id, cache_invalid=cache_invalid)
            )
        materialize = metadata.pop('materialize')

        # Check for Excel file format
        try:
            xlrd.open_workbook(dataset_path)
        except xlrd.XLRDError:
            pass
        else:
            logger.info("This is an Excel file")
            materialize.setdefault('convert', []).append({'identifier': 'xls'})
            excel_temp_path = dataset_path + '.xls'
            os.rename(dataset_path, excel_temp_path)
            try:
                with open(dataset_path, 'w', newline='') as dst:
                    xls_to_csv(excel_temp_path, dst)
            finally:
                os.remove(excel_temp_path)

        # Check for TSV file format
        with open(dataset_path, 'r') as fp:
            dialect = csv.Sniffer().sniff(fp.read(4096))
        if getattr(dialect, 'delimiter', '') == '\t':
            logger.info("This is a TSV file")
            materialize.setdefault('convert', []).append({'identifier': 'tsv'})
            tsv_temp_path = dataset_path + '.tsv'
            os.rename(dataset_path, tsv_temp_path)
            try:
                with open(dataset_path, 'w', newline='') as dst:
                    tsv_to_csv(tsv_temp_path, dst)
            finally:
                os.remove(tsv_temp_path)

        # Check for pivoted temporal table
        with open(dataset_path, 'r') as fp:
            reader = csv.reader(fp)
            columns = next(iter(reader))
        if len(columns) >= 3:
            non_matches = [
                i for i, name in enumerate(columns)
                if parse_date(name) is None
            ]
            if len(non_matches) <= max(2.0, 0.20 * len(columns)):
                logger.info("Detected pivoted table")
                materialize.setdefault('convert', []).append({
                    'identifier': 'pivot',
                    'except_columns': non_matches,
                })
                pivot_temp_path = dataset_path + '.pivot.csv'
                os.rename(dataset_path, pivot_temp_path)
                try:
                    with open(dataset_path, 'w', newline='') as dst:
                        pivot_table(pivot_temp_path, dst, non_matches)
                finally:
                    os.remove(pivot_temp_path)

        # Profile
        with profile_semaphore:
            with prom_incremented(PROM_PROFILING):
                start = time.perf_counter()
                metadata = process_dataset(
                    data=dataset_path,
                    dataset_id=dataset_id,
                    metadata=metadata,
                    lazo_client=lazo_client,
                    nominatim=nominatim,
                    include_sample=True,
                    coverage=True,
                    plots=True,
                )
                logger.info(
                    "Profiling took %.2fs",
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
        self.nominatim = os.environ['NOMINATIM_URL']
        self.channel = None

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
            port=os.environ['AMQP_PORT'],
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

            # Compare materialization info with stored to know whether cache
            # should be ignored
            try:
                hit = self.es.get('datamart', dataset_id)
            except elasticsearch.NotFoundError:
                cache_invalid = True
            else:
                cache_invalid = materialize != hit['_source']['materialize']

            future = self.loop.run_in_executor(
                None,
                materialize_and_process_dataset,
                dataset_id,
                metadata,
                self.lazo_client,
                self.nominatim,
                self.profile_semaphore,
                cache_invalid,
            )

            future.add_done_callback(
                self.process_dataset_callback(
                    message, dataset_id,
                )
            )

    def process_dataset_callback(self, message, dataset_id):
        async def coro(future):
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
                except DatasetTooBig:
                    # Materializer reached size limit
                    logger.info("Dataset over size limit: %r", dataset_id)
                    message.ack()
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
