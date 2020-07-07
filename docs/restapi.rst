Using the REST API
==================

You can access Datamart through a REST API. In addition to the documentation below, you can find a `Swagger UI <swagger/index.html>`__ which can be used to try the API.

There is also a :doc:`Python client library <python/datamart-rest>` for this API.

..  _rest-search:

``POST /search``
----------------

Queries the DataMart system for datasets.

The ``Content-Type`` should be set to `multipart/form-data <https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Disposition>`__ to allow sending both the query description and the data file.

The following keys are accepted in the request body (you need to specify at least one of them):

* ``data``: a file in a supported file format (CSV, Excel, SPSS...)
* ``data_profile``: profile information for an input dataset in JSON format (such as returned by the :doc:`python/datamart-profiler` or the :ref:`rest-profile` endpoint) or a token obtained from :ref:`rest-profile`
* ``query``: JSON object representing the query, according to :ref:`the query API specification <schema-query>`

This endpoint returns a JSON object, according to :ref:`the query results specification <schema-result>`.

..  _rest-download:

``POST /download``
------------------

Downloads a dataset from DataMart.

The ``Content-Type`` should be set to `multipart/form-data <https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Disposition>`__.

The following keys are accepted in the request body:

* ``data``: a file in a supported file format (CSV, Excel, SPSS...)
* ``task``: a JSON object that represents a query result, according to :ref:`the query results specification <schema-result>`
* ``format``: indicates the format of the returned file

If ``data`` is supplied, DataMart will return a dataset that augments well with ``data``, i.e., it will only return the portions of the dataset referenced by ``id`` that matches well with ``data``.

Additionally, you can use the ``format`` query parameter to get the result in a specific format, for example ``/download?format=d3m``:

* ``"csv"``: returns the dataset as a ``csv`` file (``application/octet-stream``); this is the default option
* ``"d3m"``: returns a ``zip`` file (``application/zip``) containing the dataset as a ``csv`` file and its corresponding ``datasetDoc.json`` file

When using the ``d3m`` format, the structure for the ZIP file follows the D3M format:

..  code::

    dataset.zip
    +-- datasetDoc.json
    +-- tables
        +-- learningData.csv

..  _rest-download-get:

``GET /download/<id>``
----------------------

Downloads a dataset from DataMart, where id is the dataset identifier. It also accepts one query parameter, ``format``, as specified above.

..  _rest-augment:

``POST /augment``
-----------------

Augments a dataset.

The ``Content-Type`` should be set to `multipart/form-data <https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Disposition>`__.

The accepted key/value pairs in the request body are the following:

* ``data``: path to a D3M dataset **OR** path to a ``csv`` file **OR** ``csv`` file contents
* ``task``: a JSON object that represents a query result, according to :ref:`the query results specification <schema-result>`
* ``columns``: a list of column indices from the DataMart dataset that will be added to ``data`` (optional)
* ``destination``: the location in disk where the new data will be saved (optional). Note that DataMart must have access to this path.

This endpoint also accepts the ``format`` query parameter, as specified for :ref:`the download endpoint <rest-download>`. However it currently defaults to the ``d3m`` format.

..  _rest-upload:

``POST /upload``
----------------

Adds a dataset to the index. The file can be provided either via a URL or direct upload.

When providing a URL, make sure it is a direct link to a file in a supported format (CSV, Excel, SPSS, ...) and not to an HTML page with a "download" button or GitHub page where the content is embedded (use the "raw" button).

The request will return the ID of the new dataset immediately, but profiling will happen in the background so the file will only appear in searches after a couple minutes::

    {"id": "datamart.upload.abcdef1234567890"}

..  _rest-profile:

``POST /profile``
-----------------

Profile a dataset. Does not add it to the index.

The computed metadata is returned, similar to using the :doc:`python/datamart-profiler` directly.

This endpoint expects one variable in the request body, ``data``, the contents of a file to be profiled in a supported file format (e.g. CSV, Excel, SPSS...).

In addition to the profile information, the returned JSON object contains a short string under the key ``token``, which can be used instead of the full data when doing searches (provide it as ``data_profile``).

..  _rest-embed:

Embedding Datamart in your software
-----------------------------------

Rather than using the API and implementing your own UI for data search and augmentation, it is possible to **re-use our web frontend**, and collect results **directly from Datamart into your system without the user downloading it and then adding it** in your interface.

This can be done using the following 3 steps (4 steps for augmentation):

(optional) Step 0: Provide your input data if searching for augmentations
*************************************************************************

If you don't have input data to provide, skip this step.

Issue a request ``POST /profile``, providing your data, and get the string under the ``token`` JSON key.

Step 1: Create a session: ``POST /session/new``
***********************************************

Issue a request ``POST /session/new``, with the following JSON input:

* ``data_token``: the token obtained from ``POST /profile``, if searching for augmentations. Optional.
* ``format``: the desired format for datasets, as specified for :ref:`the download endpoint <rest-download>`. Options go in the ``format_options`` object. Optional, defaults to ``csv``.
* ``system_name``: the name of your system. Optional, defaults to "TA3". Will be shown on butttons (e.g. "Add to <system_name>", "Join and add to <system_name>").

The result is a JSON object containing the following:

* ``session_id``: a short string identifying the session. Use this later to retrieve results.
* ``link_url``: a link to our interface that you can present the user (or embed, etc)

Step 2: Direct the user to Datamart
***********************************

Direct the user to the ``link_url`` obtained at step 1. Wait for them to be done to move to step 3, or poll step 3 regularly.

The user will be able to use our interface like normal, including using filters and related searches. The download buttons are replaced by "Add to <system_name" buttons.

Step 3: Obtain the selected data from Datamart: ``GET /session/<id>``
*********************************************************************

Issue a request to ``GET /session/<session_id>``, where ``session_id`` is the short string you obtained in step 1.

The result is an array of JSON objects, under a top-level key ``results``. Each object has a single key, ``url``, at which you can find the data that the user selected (in the format you selected at step 1).
