#!/bin/bash

while read line; do
  if [ "$line" != "" -a "${line:0:1}" != "#" ]; then
    var="$(echo "$line" | sed 's/^\([^=]\+\)=\(.*\)$/\1/')"
    val="$(echo "$line" | sed 's/^\([^=]\+\)=\(.*\)$/\2/')"
    eval "export $var=${val@Q}"
  fi
done <.env
export DATAMART_VERSION=$(git describe)
export DATAMART_GEO_DATA="$(pwd)/lib_geo/data"
