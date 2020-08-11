FROM haproxy:2.2

RUN apt-get update && apt-get install -y curl && \
    rm -rf /var/lib/apt/lists/*
