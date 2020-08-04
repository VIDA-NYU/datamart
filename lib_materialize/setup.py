import io
import os
from setuptools import setup


os.chdir(os.path.abspath(os.path.dirname(__file__)))


req = [
    'requests',
    'xlrd',
    'pyreadstat>=1.0,<2.0',
]
with io.open('README.rst', encoding='utf-8') as fp:
    description = fp.read()
setup(name='datamart_materialize',
      version='0.7',
      packages=['datamart_materialize'],
      entry_points={
          'datamart_materialize': [
              'datamart.noaa = datamart_materialize.noaa:noaa_materializer',
          ],
          'datamart_materialize.writer': [
              'csv = datamart_materialize:CsvWriter',
              'd3m = datamart_materialize.d3m:D3mWriter',
              'pandas = datamart_materialize:PandasWriter',
          ],
          'datamart_materialize.converter': [
              'xls = datamart_materialize.excel:ExcelConverter',
              'tsv = datamart_materialize.tsv:TsvConverter',
              'spss = datamart_materialize.spss:SpssConverter',
              'pivot = datamart_materialize.pivot:PivotConverter',
          ],
      },
      install_requires=req,
      description="Materialization library for Datamart",
      long_description=description,
      author="Remi Rampin",
      author_email='remi.rampin@nyu.edu',
      maintainer="Remi Rampin",
      maintainer_email='remi.rampin@nyu.edu',
      url='https://gitlab.com/ViDA-NYU/datamart/datamart',
      project_urls={
          'Homepage': 'https://gitlab.com/ViDA-NYU/datamart/datamart',
          'Source': 'https://gitlab.com/ViDA-NYU/datamart/datamart',
          'Tracker': 'https://gitlab.com/ViDA-NYU/datamart/datamart/issues',
      },
      license='Apache-2.0',
      keywords=['datamart'],
      classifiers=[
          'Development Status :: 4 - Beta',
          'Intended Audience :: Science/Research',
          'License :: OSI Approved :: Apache Software License',
          'Operating System :: OS Independent',
          'Programming Language :: Python :: 3 :: Only',
          'Topic :: Scientific/Engineering :: Information Analysis'])
