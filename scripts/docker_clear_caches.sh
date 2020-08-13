#!/bin/sh
set -eu
cd "$(dirname "$(dirname "$0")")"
PROJ="$(basename "$(pwd)")"
docker run -ti --rm --network ${PROJ}_default -v $PWD/scripts:/scripts -v $PWD/volumes/cache:/cache ${PROJ}_coordinator python /scripts/clear_caches.py "$@"
