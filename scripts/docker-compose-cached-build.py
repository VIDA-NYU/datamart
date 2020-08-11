#!/usr/bin/env python3

"""Builder for docker-compose

This essentially replicates the exact functionality of `docker-compose build`
except that it will cache properly. For some reason the COPY statements don't
hit the cache when building from docker-compose, which would mean images
getting rebuilt for every CI job.
"""

import os
import subprocess
import sys
import yaml


def main(services):
    with open('docker-compose.yml') as fp:
        config = yaml.safe_load(fp)

    version = os.environ['DATAMART_VERSION']

    for name, svc in config['services'].items():
        if services and name not in services:
            continue
        if 'image' not in svc:
            build = svc['build']
            image = '%s_%s' % (
                os.path.basename(os.getcwd()),
                name,
            )
            cmd = [
                'docker', 'build',
                '-t', image,
                '-f', os.path.join(build['context'], build['dockerfile']),
                '--cache-from=datamart_base',
                '--cache-from=datamart_npm',
                '--build-arg', 'version=%s' % version,
                build['context'],
            ]
            print(' '.join(cmd), flush=True)
            subprocess.check_call(cmd)


if __name__ == '__main__':
    main(sys.argv[1:])
