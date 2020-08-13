import os
from setuptools import setup


os.chdir(os.path.abspath(os.path.dirname(__file__)))


req = [
    'aio-pika',
    'Distance',
    'elasticsearch~=7.0',
    'redis~=3.4',
    'lazo-index-service==0.5.1',
    'prometheus_client',
    'tornado>=5.0',
    'datamart_augmentation',
    'datamart_core',
    'datamart_materialize',
    'datamart_profiler',
]
setup(name='datamart-api-service',
      version='0.0',
      packages=['apiserver'],
      entry_points={
          'console_scripts': [
              'datamart-apiserver = apiserver.main:main']},
      install_requires=req,
      description="API service of Datamart",
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
      long_description="API service of Datamart",
      license='Apache-2.0',
      keywords=['datamart'],
      classifiers=[
          'Development Status :: 4 - Beta',
          'Intended Audience :: Science/Research',
          'License :: OSI Approved :: Apache Software License',
          'Operating System :: Unix',
          'Programming Language :: Python :: 3 :: Only',
          'Topic :: Scientific/Engineering :: Information Analysis'])
