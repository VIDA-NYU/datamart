FROM pypy:3.6

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
COPY discovery/example_discovery.py discovery/test_discovery.py /usr/src/app/discovery/
COPY tests/data /usr/src/app/tests/data
ARG version
ENV DATAMART_VERSION ${version}
RUN test -n "${DATAMART_VERSION}"
ENTRYPOINT ["/tini", "--", "/bin/bash", "-c", "set -m; if [ \"$1\" = example ]; then exec python discovery/example_discovery.py; elif [ \"$1\" = testsuite ]; then exec python discovery/test_discovery.py; else exec \"$@\"; fi", "--"]
CMD ["example"]
