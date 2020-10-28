import aio_pika
import asyncio
import elasticsearch
import elasticsearch.helpers
import itertools
import json
import logging
import os
import pkg_resources
import prometheus_client
import time
import yaml

from datamart_core.common import log_future


logger = logging.getLogger(__name__)


PROM_DATASETS = prometheus_client.Gauge(
    'source_count',
    "Count of datasets per source",
    ['source'],
)
PROM_PROFILED_VERSION = prometheus_client.Gauge(
    'profiled_version_count',
    "Count of datasets per profiler version",
    ['version'],
)


NB_RECENT = 15


class RecentList(object):
    def __init__(self, size, init=None):
        self.size = size
        if init is not None:
            self.items = list(itertools.islice(init, size))
        else:
            self.items = []

    def insert_or_replace(self, key, value):
        for i in range(len(self.items)):
            if self.items[i][0] == key:
                self.items[i] = key, value
                return

        self.items.insert(0, (key, value))
        if len(self.items) > self.size:
            del self.items[self.size:]

    def __iter__(self):
        return (entry[1] for entry in self.items)


class Coordinator(object):
    def __init__(self, es):
        self.elasticsearch = es
        self._recent_discoveries = RecentList(NB_RECENT)

        # Setup the indices from YAML file
        with pkg_resources.resource_stream(
                'coordinator', 'elasticsearch.yml') as stream:
            indices = yaml.safe_load(stream)
        indices.pop('_refs', None)
        # Add custom fields
        custom_fields = os.environ.get('CUSTOM_FIELDS', None)
        if custom_fields:
            custom_fields = json.loads(custom_fields)
            if custom_fields:
                for field, opts in custom_fields.items():
                    for idx, name in [
                        ('datamart', field),
                        ('datamart_columns', 'dataset_' + field),
                        ('datamart_spatial_coverage', 'dataset_' + field),
                    ]:
                        indices[idx]['mappings']['properties'][name] = {
                            'type': opts['type'],
                        }
        # Retry a few times, in case the Elasticsearch container is not yet up
        for i in itertools.count():
            try:
                for name, index in indices.items():
                    if not es.indices.exists(name):
                        logger.info("Creating index %r in Elasticsearch",
                                    name)
                        es.indices.create(
                            name,
                            index,
                        )
            except Exception:
                logger.warning("Can't connect to Elasticsearch, retrying...")
                if i == 5:
                    raise
                else:
                    time.sleep(5)
            else:
                break

        # Create cache directories
        os.makedirs('/cache/datasets', exist_ok=True)
        os.makedirs('/cache/aug', exist_ok=True)
        os.makedirs('/cache/user_data', exist_ok=True)

        # Start AMQP coroutine
        log_future(
            asyncio.get_event_loop().create_task(self._amqp()),
            logger,
            should_never_exit=True,
        )

        # Start statistics coroutine
        self.sources_counts = {}
        self.profiler_versions_counts = {}
        log_future(
            asyncio.get_event_loop().create_task(self.update_statistics()),
            logger,
            should_never_exit=True,
        )

    def recent_discoveries(self):
        return iter(self._recent_discoveries)

    @staticmethod
    def build_discovery(dataset_id, metadata):
        materialize = metadata.get('materialize', {})
        return dict(
            id=dataset_id,
            discoverer=materialize.get('identifier', '(unknown)'),
            discovered=materialize.get('date', '???'),
            profiled=metadata.get('date', '???'),
            name=metadata.get('name'),
            types=metadata.get('types'),
        )

    async def _amqp(self):
        connection = await aio_pika.connect_robust(
            host=os.environ['AMQP_HOST'],
            port=int(os.environ['AMQP_PORT']),
            login=os.environ['AMQP_USER'],
            password=os.environ['AMQP_PASSWORD'],
        )
        self.channel = await connection.channel()
        await self.channel.set_qos(prefetch_count=1)

        # Declare profiling exchange (to publish datasets via upload)
        self.profile_exchange = await self.channel.declare_exchange(
            'profile',
            aio_pika.ExchangeType.FANOUT,
        )

        # Register to datasets exchange
        datasets_exchange = await self.channel.declare_exchange(
            'datasets',
            aio_pika.ExchangeType.TOPIC)
        self.datasets_queue = await self.channel.declare_queue(exclusive=True)
        await self.datasets_queue.bind(datasets_exchange, '#')

        await asyncio.gather(
            asyncio.get_event_loop().create_task(self._consume_datasets()),
        )

    async def _consume_datasets(self):
        # Consume dataset messages
        async for message in self.datasets_queue.iterator(no_ack=True):
            obj = json.loads(message.body.decode('utf-8'))
            dataset_id = obj['id']
            logger.info("Got dataset message: %r", dataset_id)
            self._recent_discoveries.insert_or_replace(
                dataset_id,
                self.build_discovery(dataset_id, obj),
            )

    def _update_statistics(self):
        """Periodically compute statistics.
        """
        # Load recent datasets from Elasticsearch
        recent_discoveries = []
        try:
            recent = self.elasticsearch.search(
                index='datamart',
                body={
                    'query': {
                        'match_all': {},
                    },
                    'sort': [
                        {'date': {'order': 'desc'}},
                    ],
                },
                size=NB_RECENT,
            )['hits']['hits']
        except elasticsearch.ElasticsearchException:
            logger.warning("Couldn't get recent datasets from Elasticsearch")
        else:
            for h in recent:
                recent_discoveries.append((
                    h['_id'],
                    self.build_discovery(h['_id'], h['_source']),
                ))

        # Count datasets per source
        sources = self.elasticsearch.search(
            index='datamart',
            body={
                'aggs': {
                    'sources': {
                        'terms': {
                            'field': 'source',
                            'size': 100,
                        },
                    },
                },
            },
        )['aggregations']['sources']
        sources = {
            bucket['key']: bucket['doc_count']
            for bucket in sources['buckets']
        }

        versions = self.elasticsearch.search(
            index='datamart',
            body={
                'aggs': {
                    'versions': {
                        'terms': {
                            'field': 'version',
                        },
                    },
                },
            },
            size=0,
        )['aggregations']['versions']
        versions = {
            bucket['key']: bucket['doc_count']
            for bucket in versions['buckets']
        }

        # Update prometheus
        for source, count in sources.items():
            PROM_DATASETS.labels(source).set(count)
        for source in self.sources_counts.keys() - sources.keys():
            PROM_DATASETS.remove(source)

        for version, count in versions.items():
            PROM_PROFILED_VERSION.labels(version).set(count)
        for version in self.profiler_versions_counts.keys() - versions.keys():
            PROM_PROFILED_VERSION.remove(version)

        return sources, versions, recent_discoveries

    async def update_statistics(self):
        """Periodically update statistics.
        """
        while True:
            try:
                # Compute statistics in background thread
                (
                    self.sources_counts,
                    self.profiler_versions_counts,
                    recent_discoveries,
                ) = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._update_statistics,
                )
                self._recent_discoveries = RecentList(
                    NB_RECENT,
                    recent_discoveries,
                )

                logger.info(
                    "Now %d datasets",
                    sum(self.sources_counts.values()),
                )
            except Exception:
                logger.exception("Exception computing statistics")

            await asyncio.sleep(5 * 60)
