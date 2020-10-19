import asyncio
from http.server import HTTPServer, SimpleHTTPRequestHandler
import logging
import os
import shutil
import threading

from datamart_core import Discoverer
from datamart_core.common import setup_logging


logger = logging.getLogger(__name__)


class TestDiscoverer(Discoverer):
    """Discovery plugin for the test suite.
    """
    def main_loop(self):
        # Put this one on disk
        with open('geo.csv', 'rb') as src:
            with self.write_to_shared_storage('geo') as dst:
                shutil.copyfileobj(src, dst)
        self.record_dataset(
            dict(),
            {
                # Omit name, should be set to 'geo' automatically
                'description': "Another simple CSV with places",
                'source': 'remi',
            },
            dataset_id='geo',
        )

        # Use URL for this one
        self.record_dataset(
            dict(direct_url='http://test-discoverer:7000/geo_wkt.csv'),
            {
                'name': 'geo_wkt',
                'description': "Simple CSV in WKT format",
                'source': 'remi',
            },
            dataset_id='geo_wkt',
        )

        # Put this one on disk
        with open('agg.csv', 'rb') as src:
            with self.write_to_shared_storage('agg') as dst:
                shutil.copyfileobj(src, dst)
        self.record_dataset(
            dict(),
            {
                # Omit name, should be set to 'agg' automatically
                'description': "Simple CSV with ids and salaries to test"
                               " aggregation for numerical attributes",
                'source': 'fernando',
            },
            dataset_id='agg',
        )

        # Use URL for this one
        self.record_dataset(
            dict(direct_url='http://test-discoverer:7000/lazo.csv'),
            {
                # Omit name, should be set to 'lazo' automatically
                'description': "Simple CSV with states and years"
                               " to test the Lazo index service",
                'source': 'fernando',
            },
            dataset_id='lazo',
        )

        # Use URL for this one
        self.record_dataset(
            dict(direct_url='http://test-discoverer:7000/empty.csv'),
            {
                # Omit name, should be set to 'empty' automatically
                'description': "A CSV with no rows to test alternate index",
                'source': 'remi',
            },
            dataset_id='empty',
        )

        # Put this one on disk
        with open('daily.csv', 'rb') as src:
            with self.write_to_shared_storage('daily') as dst:
                shutil.copyfileobj(src, dst)
        self.record_dataset(
            dict(),
            {
                'name': 'daily',
                'description': "Temporal dataset with daily resolution",
                'source': 'remi',
            },
            dataset_id='daily',
        )

        # Use URL for this one
        self.record_dataset(
            dict(direct_url='http://test-discoverer:7000/hourly.csv'),
            {
                # Omit name, should be set to 'hourly' automatically
                'description': "Temporal dataset with hourly resolution",
                'source': 'remi',
            },
            dataset_id='hourly',
        )

        # Use URL for this one
        self.record_dataset(
            dict(direct_url='http://test-discoverer:7000/invalid.bin'),
            {
                'name': 'Invalid, binary',
                'description': "Some binary data that can't be parsed",
                'source': 'remi',
            },
            dataset_id='invalid',
        )

        # Use URL for this one
        self.record_dataset(
            dict(direct_url='http://test-discoverer:7000/dates_pivoted.csv'),
            {
                'name': 'dates pivoted',
                'description': "Temporal dataset but in columns",
                'source': 'remi',
            },
            dataset_id='dates_pivoted',
        )

        # Use URL for this one
        self.record_dataset(
            dict(direct_url='http://test-discoverer:7000/spss.sav'),
            {
                # Omit name, should be set to 'spss' automatically
                'description': "SPSS format test",
                'source': 'remi',
            },
            dataset_id='spss',
        )

        # Use URL for this one
        self.record_dataset(
            dict(direct_url='http://test-discoverer:7000/excel.xlsx'),
            {
                # Omit name, should be set to 'excel' automatically
                'description': "Excel format test",
                'source': 'remi',
            },
            dataset_id='excel',
        )

        # Use URL for this one
        self.record_dataset(
            dict(direct_url='http://test-discoverer:7000/stata114.dta'),
            {
                # Omit name, should be set to 'stata114' automatically
                'description': "Stata format 114 test",
                'source': 'remi',
            },
            dataset_id='stata114',
        )

        # Use URL for this one
        self.record_dataset(
            dict(direct_url='http://test-discoverer:7000/stata118.dta'),
            {
                # Omit name, should be set to 'stata118' automatically
                'description': "Stata format 118 test",
                'source': 'remi',
            },
            dataset_id='stata118',
        )

        # Use URL for this one
        self.record_dataset(
            dict(direct_url='http://test-discoverer:7000/basic.csv'),
            {
                'name': "basic",
                'description': "This is a very simple CSV with people",
                'source': 'remi',
            },
            dataset_id='basic',
        )


def server():
    with HTTPServer(('0.0.0.0', 7000), SimpleHTTPRequestHandler) as httpd:
        httpd.serve_forever()


if __name__ == '__main__':
    setup_logging()
    os.chdir('tests/data')

    # Start a web server
    server_thread = threading.Thread(target=server)
    server_thread.setDaemon(True)
    server_thread.start()

    TestDiscoverer('datamart.test')
    asyncio.get_event_loop().run_forever()
