FROM python:3.8

ENV TINI_VERSION v0.18.0
RUN curl -Lo /tini https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini && \
    chmod +x /tini

ENV PYTHONFAULTHANDLER=1

RUN mkdir -p /usr/src/app/home && \
    useradd -d /usr/src/app/home -s /usr/sbin/nologin -u 998 appuser && \
    chown appuser /usr/src/app/home
WORKDIR /usr/src/app
RUN curl -sSL https://raw.githubusercontent.com/sdispater/poetry/master/get-poetry.py | python - --version 1.0.10 && $HOME/.poetry/bin/poetry config virtualenvs.create false
RUN pip --disable-pip-version-check --no-cache-dir install toml
COPY docker/install_deps.py poetry.lock /usr/src/app/
RUN python -c "import json, toml; json.dump(toml.load(open('poetry.lock')), open('poetry.lock.json', 'w'))"
RUN python3 install_deps.py poetry.lock.json

COPY --chown=appuser lib_core lib_core
RUN pip --disable-pip-version-check --no-cache-dir install --no-deps -e /usr/src/app/lib_core
COPY --chown=appuser discovery/noaa /usr/src/app/noaa
RUN pip --disable-pip-version-check --no-cache-dir install --no-deps -e noaa

RUN python -m compileall /usr/src/app/
ARG version
ENV DATAMART_VERSION ${version}
RUN test -n "${DATAMART_VERSION}"
USER 998
ENTRYPOINT ["/tini", "--"]
CMD ["python", "-m", "noaa_discovery.noaa_discovery"]
