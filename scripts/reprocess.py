#!/usr/bin/env python3

"""This script reprocesses datasets that are already in the index.

This is generally not necessary. You can use freshen_old_index.py to reprocess
datasets that were profiled by old versions of the profiler.
"""

import aio_pika
import asyncio
import elasticsearch
import logging
import os
import sys

from datamart_core.common import json2msg


logger = logging.getLogger(__name__)


async def freshen(datasets):
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

    hits = [
        es.get('datamart', d)
        for d in datasets
    ]
    for h in hits:
        obj = h['_source']
        dataset_version = obj['version']

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


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(loop.create_task(
        freshen(sys.argv[1:])
    ))
