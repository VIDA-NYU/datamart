import asyncio
import logging
import time

from datamart_core import Discoverer
from datamart_core.common import Storage


logger = logging.getLogger(__name__)


class ExampleDiscoverer(Discoverer):
    """Example discovery plugin.
    """
    data = ('name,country\n'
            'Remi,France\n'
            'Heiko,Australia\n'
            'Fernando,Brazil\n'
            'Juliana,USA\n')
    meta = {
        'filename': 'nyu.zip',
        'is_example': True,
    }

    def main_loop(self):
        # State-of-the-art search process
        logger.info("Searching...")
        time.sleep(5)

        # We found a dataset
        dataset_id = self.record_dataset(
            Storage({'path': 'storage path here'}),
            self.meta)
        logger.info("Dataset recorded: %r", dataset_id)

        # TODO: Download it

    def handle_materialize(self, discovery_meta):
        if discovery_meta['filename'] == 'nyu.zip':
            pass  # TODO


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")
    ExampleDiscoverer('org.datadrivendiscovery.example.discoverer')
    asyncio.get_event_loop().run_forever()
