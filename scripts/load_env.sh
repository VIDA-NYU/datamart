#!/bin/bash

while read line; do
  if [ "$line" != "" -a "${line:0:1}" != "#" ]; then
    export "$line"
  fi
done <.env
export DATAMART_VERSION=$(git describe)
export DATAMART_GEO_DATA="$(pwd)/lib_geo/data"
