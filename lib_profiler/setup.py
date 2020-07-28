import io
import os
from setuptools import setup


os.chdir(os.path.abspath(os.path.dirname(__file__)))


req = [
    'lazo-index-service==0.5.1',
    'numpy',
    'pandas',
    'prometheus_client',
    'python-dateutil',
    'scikit-learn>=0.22,<0.24',
    'regex',
    'requests',
    'datamart-geo==0.1',
]
with io.open('README.rst', encoding='utf-8') as fp:
    description = fp.read()
setup(name='datamart_profiler',
      version='0.6.2',
      packages=['datamart_profiler'],
      install_requires=req,
      description="Data profiling library for Datamart",
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
      long_description=description,
      license='Apache-2.0',
      keywords=['datamart'],
      classifiers=[
          'Development Status :: 4 - Beta',
          'Intended Audience :: Science/Research',
          'License :: OSI Approved :: Apache Software License',
          'Natural Language :: English',
          'Operating System :: OS Independent',
          'Programming Language :: Python :: 3 :: Only',
          'Topic :: Scientific/Engineering :: Information Analysis'])
