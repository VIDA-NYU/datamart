#!/bin/bash

export PATH="$HOME/bin:$PATH"

cd "$(dirname "$(dirname "$0")")"

set -eux

# Re-build and re-start services
docker-compose build --build-arg version=v0.0 coordinator profiler apiserver test_discoverer
docker-compose up -d coordinator
docker-compose up -d --force-recreate profiler apiserver apilb

# XXX: To run with debugger: remove 'apiserver' up here, use 'read' to block, and
# run apiserver container like so:
# docker run -ti --rm --name apiserver --network datamart_default -e ELASTICSEARCH_HOSTS=elasticsearch:9200 -e AMQP_HOST=rabbitmq -e AMQP_PORT=5672 -e AMQP_USER=${AMQP_USER} -e AMQP_PASSWORD=${AMQP_PASSWORD} -e LAZO_SERVER_HOST=lazo -e LAZO_SERVER_PORT=50051 -v $(pwd)/volumes/datasets:/datasets -v $(pwd)/volumes/cache:/cache datamart_apiserver
#echo "START DATAMART-APISERVER MANUALLY" && read i

# Clear cache
docker exec -ti $(basename "$(pwd)")_coordinator_1 sh -c 'rm -rf /cache/*/*'

# Clear index
scripts/docker_purge_source.sh datamart.test

sleep 2

# Re-profile
docker-compose up -d --force-recreate test_discoverer

# Wait for profiling to end
(set +x
slept=0; while [ $slept -le 40 -a $(curl -s -o /dev/null -w "%{http_code}" http://localhost:9200/datamart/_doc/datamart.test.basic) != 200 ]; do sleep 1; slept=$((slept + 1)); done
if [ $slept -gt 40 ]; then
  echo "Profiling didn't end after ${slept}s"
  exit 1
fi
)

# Load .env
set +x
cat .env | while read l; do [ -z "$l" ] || [ "${l:0:1}" = \# ] || echo "export $l"; done >.env.sh && . .env.sh && rm .env.sh
set -x

# Set other variables
export DATAMART_VERSION=v0.0
export DATAMART_GEO_DATA=$(pwd)/lib_geo/data

# Run tests
poetry run python tests
