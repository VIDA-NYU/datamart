import os
from setuptools import setup


os.chdir(os.path.abspath(os.path.dirname(__file__)))


req = [
    'aio-pika',
    'elasticsearch~=7.0',
    'lazo-index-service==0.6.1',
    'prometheus_client',
    'prometheus_async',
    'sentry-sdk',
]
setup(name='datamart_core',
      version='0.0',
      packages=['datamart_core'],
      install_requires=req,
      description="Core library for Auctus services",
      author="Remi Rampin",
      author_email='remi.rampin@nyu.edu',
      maintainer="Remi Rampin",
      maintainer_email='remi.rampin@nyu.edu',
      url='https://gitlab.com/ViDA-NYU/auctus/auctus',
      project_urls={
          'Homepage': 'https://gitlab.com/ViDA-NYU/auctus/auctus',
          'Source': 'https://gitlab.com/ViDA-NYU/auctus/auctus',
          'Tracker': 'https://gitlab.com/ViDA-NYU/auctus/auctus/-/issues',
      },
      long_description="Core library for Auctus services",
      license='BSD-3-Clause',
      keywords=['auctus', 'datamart'],
      classifiers=[
          'Development Status :: 4 - Beta',
          'Intended Audience :: Science/Research',
          'Operating System :: OS Independent',
          'Programming Language :: Python :: 3 :: Only',
          'Topic :: Scientific/Engineering :: Information Analysis'])
