#!/bin/bash

cd "$(dirname "$(dirname "$0")")"

set -eux

# Re-build and re-start services
docker-compose build --build-arg version=v0.0 coordinator profiler apiserver test-discoverer
docker-compose up -d coordinator
docker-compose up -d --force-recreate profiler apiserver apilb

# XXX: To run with debugger: remove 'apiserver' up here, use 'read' to block, and
# run apiserver container like so:
# docker run -ti --rm --name apiserver --network datamart_default -e DEBUG=yes -e ELASTICSEARCH_HOSTS=elasticsearch:9200 -e AMQP_HOST=rabbitmq -e AMQP_PORT=5672 -e AMQP_USER=${AMQP_USER} -e AMQP_PASSWORD=${AMQP_PASSWORD} -e REDIS_HOST=redis -e LAZO_SERVER_HOST=lazo -e LAZO_SERVER_PORT=50051 -e NOMINATIM_URL=${NOMINATIM_URL} -e FRONTEND_URL=${FRONTEND_URL} -e API_URL=${API_URL} -e CUSTOM_FIELDS="${CUSTOM_FIELDS}" -v $(pwd)/volumes/datasets:/datasets -v $(pwd)/volumes/cache:/cache datamart_apiserver
#echo "START DATAMART-APISERVER MANUALLY" && read i

# Clear cache
docker exec -ti $(basename "$(pwd)")_coordinator_1 sh -c 'rm -rf /cache/*/*'
docker-compose exec redis redis-cli flushall

# Clear index
scripts/docker_purge_source.sh datamart.test
scripts/docker_purge_source.sh datamart.upload

sleep 2

# Re-profile
docker-compose up -d --force-recreate test-discoverer

# Wait for profiling to end
(set +x
slept=0
while [ $(curl -s -o /dev/null -w "%{http_code}" http://localhost:9200/datamart/_doc/datamart.test.basic) != 200 ]; do
  if [ $slept -gt 180 ]; then
    echo "Profiling didn't end after ${slept}s"
    exit 1
  fi
  sleep 1; slept=$((slept + 1))
done
)

# Load .env
set +x
. scripts/load_env.sh
set -x
export DATAMART_VERSION=v0.0

# Run tests
poetry run python tests
