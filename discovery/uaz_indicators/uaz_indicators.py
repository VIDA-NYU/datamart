import asyncio
import uuid
from datetime import datetime, timedelta
import elasticsearch.helpers
import logging
import os
import pandas
import requests
import sqlite3
import tempfile
import time

from datamart_core import Discoverer
from datamart_core.common import setup_logging


logger = logging.getLogger(__name__)


class UazIndicatorsDiscoverer(Discoverer):
    """Discoverer for University of Arizona's indicator database.

    https://ml4ai.github.io/delphi/delphi_database.html
    """
    CHECK_INTERVAL = timedelta(days=1)
    NAMESPACE = uuid.UUID('d20b45e8-b0d5-4b17-a6c4-e8399afc2afa')  # Random

    def main_loop(self):
        while True:
            now = datetime.utcnow()

            try:
                self.get_data()
            except Exception:
                logger.exception("Error getting datasets")

            sleep_until = now + self.CHECK_INTERVAL
            logger.info("Sleeping until %s", sleep_until.isoformat())
            while datetime.utcnow() < sleep_until:
                time.sleep((sleep_until - datetime.utcnow()).total_seconds())

    def get_data(self):
        # Get current E-Tag
        try:
            info = self.elasticsearch.get(
                'pending',
                self.identifier,
            )['_source']
        except elasticsearch.NotFoundError:
            etag = None
        else:
            etag = info['etag']

        # Do HTTP request
        headers = {
            'User-Agent': 'Auctus/%s' % os.environ['DATAMART_VERSION'],
        }
        if etag:
            headers['If-None-Match'] = etag
        logger.info("Downloading file (etag=%r)", etag)
        response = requests.get(
            'http://vanga.sista.arizona.edu/delphi_data/delphi.db',
            headers=headers,
            stream=True,
        )
        response.raise_for_status()
        if response.status_code == 304:
            logger.info("File hasn't changed")
            return
        logger.info(
            "Got response, length=%s",
            response.headers.get('content-length', "unset"),
        )

        with tempfile.NamedTemporaryFile(suffix='.sqlite3') as tmp:
            for chunk in response.iter_content(4096):
                if chunk:  # filter out keep-alive chunks
                    tmp.write(chunk)
            tmp.flush()

            self.discover_indicators(tmp.name)
            self.discover_dssat(tmp.name)

        self.elasticsearch.index(
            'pending',
            {'etag': response.headers.get('ETag')},
            id=self.identifier,
        )

    def discover_indicators(self, filename):
        conn = sqlite3.connect(filename)
        indicators = iter(conn.execute('''\
            SELECT DISTINCT Variable, Unit, Source
            FROM indicator
            ORDER BY Source, Variable;
        '''))
        current_prefix = None
        current_source = None
        variables = []
        for variable_name, unit, source in indicators:
            idx = variable_name.find(',')
            if idx == -1:
                prefix = variable_name
            else:
                prefix = variable_name[:idx]

            # If we're still on the same prefix, add it to the variables
            if current_prefix is None:
                current_prefix = prefix
                current_source = source
            elif prefix != current_prefix or source != current_source:
                self.make_indicator_dataset(
                    current_prefix, current_source,
                    conn, variables,
                )
                current_prefix = prefix
                current_source = source
                variables = []

            variables.append((variable_name, unit))

        if variables:
            self.make_indicator_dataset(
                current_prefix, current_source,
                conn, variables,
            )

    def make_indicator_dataset(self, variable_prefix, source, conn, variables):
        name = '%s (%s)' % (variable_prefix, source)
        dataset_id = uuid.uuid5(self.NAMESPACE, name).hex
        logger.info(
            "Making indicator dataset %r %r variables=%r",
            variable_prefix, source, variables,
        )

        # Read data using Pandas
        dataframes = []
        for variable_name, unit in variables:
            dataframes.append(pandas.read_sql_query(
                '''\
                SELECT
                    Country, State, County, Year, Month,
                    Variable, Unit,
                    Value
                FROM indicator
                WHERE Variable = ? AND Unit = ? AND Source = ?;
                ''',
                conn,
                params=(variable_name, unit, source),
            ))
        df = pandas.concat(dataframes, axis=0)

        # Make a single column 'name (unit)'
        df['Variable'] = df.apply(
            lambda row: "%s (%s)" % (row['Variable'], row['Unit']),
            axis=1,
        )
        df.drop(['Unit'], axis=1, inplace=True)

        # Pivot the concatenated data, put variables in different columns
        df.index.name = '_dummy_index'
        df = df.set_index(
            [
                'Country', 'State', 'County', 'Year', 'Month',
                'Variable',
            ],
            append=True,
        )
        df = df.unstack(level=[-1])
        df.columns = df.columns.get_level_values(1)
        df.columns.name = None
        df = df.reset_index()
        df = df.drop(['_dummy_index'], axis=1)
        df = df.sort_values(['Country', 'State', 'County', 'Year', 'Month'])

        with self.write_to_shared_storage(dataset_id) as tmp:
            df.to_csv(
                tmp,
                index=False,
                line_terminator='\r\n',
            )

        self.record_dataset(
            dict(
                uaz_indicators_variable_prefix=variable_prefix,
                uaz_source=source,
            ),
            dict(
                name=name,
                source="%s (UAZ)" % source,
            ),
            dataset_id=dataset_id,
        )

    def discover_dssat(self, filename):
        pass  # TODO


if __name__ == '__main__':
    setup_logging()
    UazIndicatorsDiscoverer('datamart.uaz-indicators')
    asyncio.get_event_loop().run_forever()
