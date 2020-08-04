#!/usr/bin/env python3

"""This script reprocesses datasets that were profiled by old versions.

If datasets don't change, they generally don't get re-profiled. This means
their metadata in the index might have been extracted with an old version of
the profiler. This script reprocesses the datasets that are from a version
older than a specified commit.
"""

import aio_pika
import asyncio
import elasticsearch
import elasticsearch.helpers
import logging
import os
import subprocess
import sys

from datamart_core.common import json2msg


logger = logging.getLogger(__name__)


SIZE = 10000


_more_recent_cache = {}


def is_version_more_recent(old, new):
    cache = _more_recent_cache.setdefault(old, {})
    if new in cache:
        return cache[new]
    else:
        more_recent = subprocess.call(
            (
                '[ "$(git merge-base {0} {1})" = ' +
                '"$(git rev-parse {0}^{{commit}})" ]'
            ).format(
                old, new,
            ),
            shell=True,
        )
        if more_recent not in (0, 1):
            raise subprocess.CalledProcessError(more_recent, 'git')
        logger.debug("%s %s %s", old, ['<=', '>'][more_recent], new)
        more_recent = more_recent == 0
        cache[new] = more_recent
        return more_recent


async def freshen(version):
    # Check that it's a valid version
    version_hash = subprocess.check_output(['git', 'rev-parse', version])
    version_hash = version_hash.decode('ascii').strip()
    logger.warning("Reprocessing datasets profiled before %s", version_hash)

    es = elasticsearch.Elasticsearch(
        os.environ['ELASTICSEARCH_HOSTS'].split(',')
    )

    amqp_conn = await aio_pika.connect_robust(
        host=os.environ['AMQP_HOST'],
        port=int(os.environ['AMQP_PORT']),
        login=os.environ['AMQP_USER'],
        password=os.environ['AMQP_PASSWORD'],
    )
    amqp_chan = await amqp_conn.channel()
    amqp_profile_exchange = await amqp_chan.declare_exchange(
        'profile',
        aio_pika.ExchangeType.FANOUT,
    )

    hits = elasticsearch.helpers.scan(
        es,
        index='datamart',
        query={
            'query': {
                'match_all': {},
            },
        },
        size=SIZE,
    )
    reprocessed = 0
    for h in hits:
        obj = h['_source']
        dataset_version = obj['version']
        if is_version_more_recent(version, dataset_version):
            logger.debug("%s is recent enough (version=%r)",
                         h['_id'], dataset_version)
            continue

        reprocessed += 1
        logger.info("Reprocessing %s, version=%r",
                    h['_id'], dataset_version)
        metadata = dict(name=obj['name'],
                        materialize=obj['materialize'],
                        source=obj.get('source', 'unknown'))
        if obj.get('description'):
            metadata['description'] = obj['description']
        if obj.get('date'):
            metadata['date'] = obj['date']
        if obj.get('manual_annotations'):
            metadata['manual_annotations'] = obj['manual_annotations']
        await amqp_profile_exchange.publish(
            json2msg(dict(id=h['_id'], metadata=metadata)),
            '',
        )
    logger.info("Reprocessed %d datasets", reprocessed)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(loop.create_task(
        freshen(sys.argv[1])
    ))
