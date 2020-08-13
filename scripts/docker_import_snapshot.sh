#!/bin/sh
set -eu
cd "$(dirname "$(dirname "$0")")"
PROJ="$(basename "$(pwd)")"
docker run -ti --rm --network ${PROJ}_default -v $PWD/scripts:/scripts -e ELASTICSEARCH_HOSTS=elasticsearch:9200 -e AMQP_HOST=rabbitmq -e AMQP_PORT=5672 -e AMQP_USER=$AMQP_USER -e AMQP_PASSWORD=$AMQP_PASSWORD -w /tmp ${PROJ}_coordinator sh -c 'curl -LO https://auctus.vida-nyu.org/snapshot/index.tar.gz && if [ -e index.snapshot ]; then rm -rf index.snapshot; fi && mkdir index.snapshot && tar xfC index.tar.gz index.snapshot && python /scripts/import_all.py index.snapshot; rm -rf index.snapshot'
