#!/usr/bin/env python
"""aws-az-failure-chaostoolkit extension builder and installer"""

import sys
import io

import setuptools

name = 'aws-az-failure-chaostoolkit'
desc = 'Custom Chaos Toolkit extension to simulate AZ failure on AWS resources'

with io.open('README.md', encoding='utf-8') as strm:
    long_desc = strm.read()

classifiers = [
    'Development Status :: 4 - Beta',
    'Intended Audience :: Developers',
    'License :: Freely Distributable',
    'Operating System :: OS Independent',
    'License :: OSI Approved :: Apache Software License',
    'Programming Language :: Python',
    'Programming Language :: Python :: 3',
    'Programming Language :: Python :: 3.5',
    'Programming Language :: Python :: 3.6',
    'Programming Language :: Python :: Implementation',
    'Programming Language :: Python :: Implementation :: CPython'
]
author = "Farhan Angullia"
author_email = 'angullia@amazon.com'
license = 'Apache License Version 2.0'
packages = setuptools.find_packages(include=['azchaosaws', 'azchaosaws.*'])
url = 'https://github.com/aws-samples/aws-az-failure-chaostoolkit'
download_url = 'https://pypi.python.org/pypi/aws-az-failure-chaostoolkit'

needs_pytest = set(['pytest', 'test']).intersection(sys.argv)
pytest_runner = ['pytest_runner'] if needs_pytest else []

test_require = []
with io.open('requirements-dev.txt') as f:
    test_require = [l.strip() for l in f if not l.startswith('#')]

install_require = []
with io.open('requirements.txt') as f:
    install_require = [l.strip() for l in f if not l.startswith('#')]

setup_params = dict(
    name=name,
    version='0.1.0',
    description=desc,
    long_description=long_desc,
    long_description_content_type='text/markdown',
    classifiers=classifiers,
    author=author,
    author_email=author_email,
    url = url,
    download_url = download_url,
    license=license,
    packages=packages,
    include_package_data=True,
    install_requires=install_require,
    tests_require=test_require,
    setup_requires=pytest_runner,
    python_requires='>=3.5.*'
)


def main():
    """Package installation entry point."""
    setuptools.setup(**setup_params)


if __name__ == '__main__':
    main()
