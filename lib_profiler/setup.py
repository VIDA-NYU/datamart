import io
import os
from setuptools import setup


os.chdir(os.path.abspath(os.path.dirname(__file__)))


req = [
    'numpy',
    'pandas',
    'prometheus_client',
    'python-dateutil',
    'scikit-learn>=0.22,<0.24',
    'regex',
    'requests',
    'datamart-geo==0.2',
]
with io.open('README.rst', encoding='utf-8') as fp:
    description = fp.read()
setup(name='datamart_profiler',
      version='0.8.1',
      packages=['datamart_profiler'],
      install_requires=req,
      description="Data profiling library for Auctus",
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
      long_description=description,
      license='Apache-2.0',
      keywords=['auctus', 'datamart'],
      classifiers=[
          'Development Status :: 4 - Beta',
          'Intended Audience :: Science/Research',
          'License :: OSI Approved :: Apache Software License',
          'Operating System :: OS Independent',
          'Programming Language :: Python :: 3 :: Only',
          'Topic :: Scientific/Engineering :: Information Analysis'])
