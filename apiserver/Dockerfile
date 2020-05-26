FROM python:3.7

ENV TINI_VERSION v0.18.0
RUN curl -Lo /tini https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini && \
    chmod +x /tini

RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app
RUN curl -sSL https://raw.githubusercontent.com/sdispater/poetry/master/get-poetry.py | python && /root/.poetry/bin/poetry config virtualenvs.create false
RUN pip --disable-pip-version-check install toml
COPY docker/install_deps.py poetry.lock /usr/src/app/
RUN python -c "import json, toml; json.dump(toml.load(open('poetry.lock')), open('poetry.lock.json', 'w'))"
RUN python3 install_deps.py poetry.lock.json
# CI: RUN pip --disable-pip-version-check install coverage==5.1
# CI: COPY .coveragerc /usr/src/app/

COPY lib_core lib_core
RUN pip --disable-pip-version-check install --no-deps -e /usr/src/app/lib_core
COPY lib_geo lib_geo
RUN pip --disable-pip-version-check install --no-deps -e /usr/src/app/lib_geo
ENV DATAMART_GEO_DATA /usr/src/app/lib_geo/data
COPY lib_materialize lib_materialize
RUN pip --disable-pip-version-check install --no-deps -e /usr/src/app/lib_materialize
COPY lib_augmentation lib_augmentation
RUN pip --disable-pip-version-check install --no-deps -e /usr/src/app/lib_augmentation
COPY lib_profiler lib_profiler
RUN pip --disable-pip-version-check install --no-deps -e /usr/src/app/lib_profiler
COPY apiserver apiserver
RUN pip --disable-pip-version-check install --no-deps -e /usr/src/app/apiserver
ARG version
ENV DATAMART_VERSION ${version}
RUN test -n "${DATAMART_VERSION}"
ENTRYPOINT ["/tini", "--"]
CMD ["datamart-apiserver"]
