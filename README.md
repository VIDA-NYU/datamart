Datamart
========

This project is a web crawler and search engine for datasets, specifically meant for data augmentation tasks in machine learning. It is able to find datasets in different repositories and index them for later retrieval.

[Documentation is available here](https://docs.auctus.vida-nyu.org/)

It is divided in multiple components:

* Libraries
  * [Client library](https://gitlab.com/ViDA-NYU/datamart/api) `datamart`. This can be installed by clients to query the Datamart server. It is able to perform profiling and materialization locally, if the corresponding libraries are installed. This lives in a separate repository to ease collaboration.
  * [Geospatial database](https://gitlab.com/ViDA-NYU/datamart/datamart-geo) `datamart_geo`. This contains data about administrative areas extracted from Wikidata and OpenStreetMap. It lives in its own repository and is used here as a submodule.
  * [Profiling library](lib_profiler/) `datamart_profiler`. This can be installed by clients, will allow the client library to profile datasets locally instead of sending them to the server. It is also used by the apiserver and profiler services.
  * [Materialization library](lib_materialize/) `datamart_materialize`. This is used to materialize dataset from the various sources that Datamart supports. It can be installed by clients, which will allow them to materialize datasets locally instead of using the server as a proxy.
  * [Data augmentation library](lib_augmentation/) `datamart_augmentation`. This performs the join or union of two datasets and is used by the apiserver service, but could conceivably be used stand-alone.
  * [Core server library](lib_core/) `datamart_core`. This contains common code for services. Only used for the server components.
* Services
  * [**Discovery services**](discovery/): those are responsible for discovering datasets. Each plugin can talk to a specific repository. *Materialization metadata* is recorded for each dataset, to allow future retrieval of that dataset.
  * [**Profiler**](profiler/): this service downloads a discovered dataset and computes additional metadata that can be used for search (for example, dimensions, semantic types, value distributions). Uses the profiling and materialization libraries.
  * **Lazo Server**: this service is responsible for indexing textual and categorical attributes using [Lazo](https://github.com/mitdbg/lazo). The code for the server and client is available [here](https://gitlab.com/ViDA-NYU/datamart/lazo-index-service).
  * [**apiserver**](apiserver/): this service responds to requests from clients to search for datasets in the index (triggering on-demand query by discovery services that support it), upload new datasets, profile datasets, or perform augmentation. Uses the profiling and materialization libraries. Implements a JSON API using the Tornado web framework.
  * [The **coordinator**](coordinator/): this service is in charge of the dataset cache, where discovery plugins download datasets, and that is read by the profiler and apiserver services. It also exports system metrics for Prometheus, and in the future will allow the administrator to perform some tasks from a browser instead of having to run scripts.
  * [The **frontend**](frontend/): this is a React app implementing a user-friendly web interface on top of the API.

![Datamart Architecture](docs/architecture.png)

Elasticsearch is used as the search index, storing one document per known dataset.

The services exchange messages through `RabbitMQ`, allowing us to have complex messaging patterns with queueing and retrying semantics, and complex patterns such as the on-demand querying.

![AMQP Overview](docs/amqp.png)

Deployment
==========

The system is currently running at https://auctus.vida-nyu.org/. You can see the system status at https://grafana.auctus.vida-nyu.org/.

Local deployment / development setup
====================================

To deploy the system locally using docker-compose, follow those step:

Set up environment
------------------

Make sure you have checked out the submodule with `git submodule init && git submodule update`

Make sure you have [Git LFS](https://git-lfs.github.com/) installed and configured (`git lfs install`)

Copy env.default to .env and update the variables there. You might want to update the password for a production deployment.

Make sure your node is set up for running Elasticsearch. You will probably have to [raise the mmap limit](https://www.elastic.co/guide/en/elasticsearch/reference/7.6/vm-max-map-count.html).

The `API_URL` is the URL at which the apiserver containers will be visible to clients. In a production deployment, this is probably a public-facing HTTPS URL. It can be the same URL that the "coordinator" component will be served at if using a reverse proxy (see [nginx.conf](nginx.conf)).

To run scripts locally, you can load the environment variables into your shell by running: `. scripts/load_env.sh` (that's *dot space scripts...*)

Build the containers
--------------------

```
$ docker-compose build --build-arg version=$(git describe) coordinator profiler apiserver frontend socrata zenodo
```

Start the base containers
-------------------------

```
$ docker-compose up -d elasticsearch rabbitmq redis minio lazo
```

These will take a few seconds to get up and running. Then you can start the other components:

```
$ docker-compose up -d coordinator profiler apiserver apilb frontend
```

You can use the `--scale` option to start more profiler or apiserver containers, for example:

```
$ docker-compose up -d --scale profiler=4 --scale apiserver=8 coordinator profiler apiserver apilb frontend
```

Ports:
* The web interface is at http://localhost:8001
* The API at http://localhost:8002/api/v1 (behind HAProxy)
* Elasticsearch is at http://localhost:8020
* The Lazo server is at http://localhost:8030
* The RabbitMQ management interface is at http://localhost:8010
* The RabbitMQ metrics are at http://localhost:8012
* The Minio interface is at http://localhost:8050 (if you use that)
* The HAProxy statistics are at http://localhost:8004
* Prometheus is at http://localhost:8040
* Grafana is at http://localhost:8041

Import a snapshot of our index (optional)
-----------------------------------------

```
$ scripts/docker_import_snapshot.sh
```

This will download an Elasticsearch dump from auctus.vida-nyu.org and import it into your local Elasticsearch container.

Start discovery plugins (optional)
----------------------------------

```
$ docker-compose up -d socrata zenodo
```

Start metric dashboard (optional)
---------------------------------

```
$ docker-compose up -d elasticsearch_exporter prometheus grafana
```

Prometheus is configured to automatically find the containers (see [prometheus.yml](docker/prometheus.yml))

A custom RabbitMQ image is used, with added plugins (management and prometheus).
