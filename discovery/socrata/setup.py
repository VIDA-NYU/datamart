import os
from setuptools import setup


os.chdir(os.path.abspath(os.path.dirname(__file__)))


req = [
    'elasticsearch~=7.0',
    'sodapy',
    'datamart_core',
]
setup(name='datamart-socrata_discovery-service',
      version='0.0',
      py_modules=['socrata_discovery'],
      install_requires=req,
      description="Socrata discovery service for Auctus",
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
      long_description="Socrata discovery service for Auctus",
      license='Apache-2.0',
      keywords=['auctus', 'datamart'],
      classifiers=[
          'Development Status :: 4 - Beta',
          'Intended Audience :: Science/Research',
          'License :: OSI Approved :: Apache Software License',
          'Operating System :: Unix',
          'Programming Language :: Python :: 3 :: Only',
          'Topic :: Scientific/Engineering :: Information Analysis'])
