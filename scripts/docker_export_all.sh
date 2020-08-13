#!/bin/sh
set -eu
cd "$(dirname "$(dirname "$0")")"
PROJ="$(basename "$(pwd)")"
if [ -z "$1" ]; then
    echo "Missing argument" >&2
    exit 1
fi
mkdir "$1"
chown 998 "$1"
docker run --rm --network ${PROJ}_default -v $PWD/scripts:/scripts -v "$1:/index" -w /index -e ELASTICSEARCH_HOSTS=elasticsearch:9200 ${PROJ}_coordinator python /scripts/export_all.py
