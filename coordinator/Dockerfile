FROM pypy:3.6

ENV TINI_VERSION v0.18.0
RUN curl -Lo /tini https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini && \
    chmod +x /tini

RUN mkdir -p /usr/src/app/home && \
    useradd -d /usr/src/app/home -s /usr/sbin/nologin -u 998 appuser && \
    chown appuser /usr/src/app/home
WORKDIR /usr/src/app
RUN curl -sSL https://raw.githubusercontent.com/sdispater/poetry/master/get-poetry.py | python && $HOME/.poetry/bin/poetry config virtualenvs.create false
RUN pip --disable-pip-version-check install toml
COPY docker/install_deps.py poetry.lock /usr/src/app/
RUN python -c "import json, toml; json.dump(toml.load(open('poetry.lock')), open('poetry.lock.json', 'w'))"
RUN python install_deps.py poetry.lock.json
# CI: RUN pip --disable-pip-version-check install coverage==5.1
# CI: COPY .coveragerc /usr/src/app/

COPY --chown=appuser lib_core lib_core
RUN pip --disable-pip-version-check install --no-deps -e /usr/src/app/lib_core
COPY --chown=appuser lib_materialize lib_materialize
RUN pip --disable-pip-version-check install --no-deps -e /usr/src/app/lib_materialize
COPY --chown=appuser coordinator coordinator
RUN pip --disable-pip-version-check install --no-deps -e /usr/src/app/coordinator
ARG version
ENV DATAMART_VERSION ${version}
RUN test -n "${DATAMART_VERSION}"
USER 998
ENTRYPOINT ["/tini", "--"]
CMD ["coordinator"]
