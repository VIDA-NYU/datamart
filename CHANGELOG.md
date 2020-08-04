0.7 (2020-08-04)
================

Incompatible changes:
* Add a Zenodo discovery plugin (can be configured with a keyword)
* Renamed docker-compose services to use dashes `-` instead of underscores `_`
* Containers no longer run as root, change permissions on volumes to uid 998
* Changed Elasticsearch index settings, use `scripts/es_reindex.sh` to update
* Changed Redis cache from Pickle to JSON, make sure to clear Redis on update

Enhancements:
* Remove latitude/longitude semantic types from unmatched columns
* Recognize "Well-Known Text" (WKT) and "Socrata combined" point formats
* Send batch queries to Nominatim, retry smaller batches on 500
* While a profiler is processing, have it download the next dataset in parallel
* Automatically handle TSV files
* Automatically pivot files with columns for years
* Add a new alternate index "pending" for non-profiled datasets (uploaded and waiting, or failed)
* Add discovery plugin for indicators from University of Arizona
* Recognize columns called "year" as temporal
* Add plot for text columns (with most common words)
* Aggregate search hits in frontend (same dataset with different augmentations)
* Remove clusters of outliers when computing ranges
* Show plots on frontend again
* Recognize datetime strings in YYYYMMDD format (even though they are valid integers)
* Fix searching on column names
* Detect named administrative areas via the datamart-geo database
* Accept format parameters for augmentations (don't only return D3M format)
* Automatically handle SPSS files
* Convert search input the same as discovered datasets (so searching is possible from a TSV, Excel, or SPSS file)
* Serve files with the correct extension
* Filter results by temporal granularity
* Cache data sent to `/profile`, to allow augmentations from it without reupload
* Update URL in frontend to reflect current query
* Add a "TA3 API" allowing systems to use our search frontend as part of their workflow, and collect results directly from Datamart afterwards
* Add "custom fields" which can be added to the upload form via configuration
* Allow the user to preview the profiled information on the upload form, and manually override column metadata
* Have lib_augmentation accept file objects
* Implement spatial joins using a KDTree to align the right dataset to the nearest point of the left dataset
* Implement joins on multiple columns (e.g. spatio-temporal)
* Re-license Datamart under Apache-2.0
* Allow searching by named spatial area in API
* Decode dates in Excel files, from the floating-point number of days format

0.6 (2020-04-29)
================

Incompatible changes:
* Updated Elasticsearch from 6 to 7. You should recreate your index from
scratch, or by importing the JSON files.
* Fix semantic type for enumeration (`http:` not `https:`)
* `docker-compose.yml` now uses local mounts for volumes (create them with `scripts/setup.sh`)
* Aggregation now always happens, even if there are no duplicate rows (so you'll always get the min/max/mean columns)
* Aggregated columns renamed (`amin` -> `min`, `amax` -> `max`)
* Renamed `lazo_server` container to `lazo`
* Updated to MIT-LL D3M schema version 4.0.0
* Added 'source' key to schema, you will have to manually add this to the indexed documents (or just re-profile)
* Added a Redis server, used to store profiling information
* Profiling now needs a Nominatim server, to resolve addresses
* New frontend, served as static JavaScript app that uses the API directly, in new container `frontend`
* Renamed `query` container to `apiserver`
* Updated Lazo to transform strings to lowercase (index needs to be recreated)

Enhancements:
* Compute additional metadata `missing_values_ratio`, `unclean_values_ratio`, `num_distinct_values`
* Don't drop any column from supplied data during union
* Checked-in Grafana settings for anonymous access, SMTP, and the dashboards we use (in `contrib/grafana-dashboards`)
* Improvements to caching code
* Use a fixed seed for sampling in profiling (makes it deterministic)
* Store a sample of each dataset, show it in search result API (and dataset page)
* Don't drop non-numeric columns in aggregation
* Clear old entries from caches when a limit is reached (`MAX_CACHE_BYTES`)
* Datasets too big to be joined will no longer be profiled into the index
* Add 'temporal' and 'spatial' badges on datasets
* Fix aggregation breaking with missing data in join column
* Added Kubernetes config (`contrib/k8s/`)
* Don't ignore Lazo errors on profiling (you will now see errors if using Lazo and it's not responding). Have it re-try on Elasticsearch errors
* Correctly deal with empty datasets
* Move additional endpoints to REST-JSON and the API container (upload, statistics)
* Improved logging (quiet Elasticsearch)
* Get lat/long from addresses to include in spatial coverage
* Add option to generate d3mIndex column, to use dataset as ML input
* Allow ability to refer to already-profiled input data using a token instead of re-uploading
* Many improvements to spatial profiling, added new aggregation resolutions (weekly, monthly, yearly)
* Profiler detects and indexes temporal resolution of datetime columns
* Can search for datasets related to another dataset in the index (by sending only its ID)
* Add selection of aggregation functions to the API (`agg_functions` list in `augmentation` dict) and frontend

0.5 (2019-08-28)
================

Re-submission at DARPA's request, to add support for categorical data. Used for evaluation.

Incompatible changes:
* Added a `lazo_server` component
* `/metadata/` endpoint now puts metadata under `metadata` key

Enhancements:
* Docker containers should now shut down properly
* Fix a problem with the type identification thresholds
* Add caching
* Index text data using Lazo
* Return metadata in D3M Dataset format under `d3m_dataset_description` key

0.4 (2019-07-19)
================

Final version for the Summer workshop, submitted for evaluation.
