#!/bin/sh
set -eu
cd "$(dirname "$(dirname "$0")")"
PROJ="$(basename "$(pwd)")"
if [ -z "$1" ]; then
    echo "Missing argument" >&2
    exit 1
fi
docker run -ti --rm --network ${PROJ}_default -v $PWD/scripts:/scripts -v "$1:/index" -e ELASTICSEARCH_HOSTS=elasticsearch:9200 -e AMQP_HOST=rabbitmq -e AMQP_PORT=5672 -e AMQP_USER=$AMQP_USER -e AMQP_PASSWORD=$AMQP_PASSWORD -e LAZO_SERVER_HOST=lazo -e LAZO_SERVER_PORT=50051 ${PROJ}_coordinator python /scripts/import_all.py /index
