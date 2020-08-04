#!/bin/bash

set -eu
cd "$(dirname "$(dirname "$0")")"
. scripts/load_env.sh

# Re-create the Elasticsearch indexes using the _reindex mechanism
# This is an Elasticsearch re-index, not a Datamart one! The JSON document are
# reprocessed without any change, and without any dataset being examined

ES_SERVER="$(echo "$ELASTICSEARCH_HOSTS" | sed 's/,.*$//')"
ES_SERVER="http://${ES_SERVER}"
echo "Using Elasticsearch server $ES_SERVER" >&2

INDEXES="datamart datamart_columns datamart_spatial_coverage pending"

####################
# ES functions

delete_index(){
  echo "Delete $1" >&2
  code="$(curl -s -X DELETE -o /dev/null -w '%{http_code}' "${ES_SERVER}/$1")"
  if [ "$code" != 200 ]; then
    echo "Failed: $code" >&2
    exit 1
  fi
}

copy_index(){
  echo "Copy $1 -> $2" >&2
  code="$(curl -s -X POST -o /dev/null -w '%{http_code}' "${ES_SERVER}/$1/_clone/$2")"
  if [ "$code" != 200 ]; then
    echo "Failed: $code" >&2
    exit 1
  fi
}

re_index(){
  echo "Re-index $1 -> $2" >&2
  code="$(curl -s -X POST -o /dev/null -w '%{http_code}' -H Content-type:application/json -d '{"source": {"index": "'"$1"'"}, "dest": {"index": "'"$2"'"}}' "${ES_SERVER}/_reindex")"
  if [ "$code" != 200 ]; then
    echo "Failed: $code"
    exit 1
  fi
}

####################


# Take all the containers down first (not elasticsearch)
echo >&2
echo "!!! If Datamart containers are running, press Ctrl+C now and turn them off !!!" >&2
echo >&2

# Set all indexes to read-only
for idx in $INDEXES; do
  code="$(curl -s -X PUT -o /dev/null -w '%{http_code}' -H Content-type:application/json -d '{"index.blocks.write": true}' "${ES_SERVER}/$idx/_settings")"
  if [ "$code" != 200 ]; then
    echo "Failed: $code"
    exit 1
  fi
done

# Remove old indexes
for idx in $INDEXES; do
  code="$(curl -s -I -o /dev/null -w '%{http_code}' "${ES_SERVER}/cloned_$idx")"
  if [ "$code" = 200 ]; then
    delete_index "cloned_$idx"
  elif [ "$code" != 404 ]; then
    echo "Failed: $code" >&2
    exit 1
  fi
done

echo "About to copy indexes, press enter to proceed" >&2
read i

for idx in $INDEXES; do
  copy_index "$idx" "cloned_$idx"
done

echo "About to delete indexes, press enter to proceed" >&2
read i

for idx in $INDEXES; do
  delete_index "$idx"
done

echo "Starting coordinator to create update index mappings" >&2
docker-compose up -d coordinator

echo "About to re-index, press enter to proceed" >&2
read i

for idx in $INDEXES; do
  re_index "cloned_$idx" "$idx"
done

echo "About to delete cloned indexes, press enter to proceed" >&2
read i

for idx in $INDEXES; do
  delete_index "cloned_$idx"
done

# Set all indexes back to read-write
for idx in $INDEXES; do
  code="$(curl -s -X PUT -o /dev/null -w '%{http_code}' -H Content-type:application/json -d '{"index.blocks.write": false}' "${ES_SERVER}/$idx/_settings")"
  if [ "$code" != 200 ]; then
    echo "Failed: $code"
    exit 1
  fi
done
