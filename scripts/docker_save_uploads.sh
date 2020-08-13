#!/bin/sh
set -eu
cd "$(dirname "$(dirname "$0")")"
PROJ="$(basename "$(pwd)")"
docker run -ti --rm --network ${PROJ}_default -v $PWD/volumes/datasets:/datasets ${PROJ}_coordinator sh -c 'tar zc /datasets/datamart.upload.*' >uploads.tar.gz
