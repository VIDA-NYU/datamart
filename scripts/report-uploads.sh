#!/bin/sh

set -eu

# Query elasticsearch
RECORD="$(curl -s -H content-type:application/json -d '{"query":{"bool":{"should":[{"term":{"materialize.identifier":"datamart.url"}},{"term":{"materialize.identifier":"datamart.upload"}}]}}, "_source":["date", "name"]}' http://localhost:9200/_search?size=1000 \
    | jq -r '.hits.hits | sort_by(._source.date)[] | ._source.date + ": " + ._id + " (" + ._source.name + ")"' \
    | tail -n 1)"
LASTRECORD="$(cat $HOME/report-uploads.last)"
if [ "$RECORD" != "$LASTRECORD" ]; then
    echo "Check https://auctus.vida-nyu.org/" \
        | mail -s "New uploaded datasets" root
    echo "$RECORD" >$HOME/report-uploads.last
fi
