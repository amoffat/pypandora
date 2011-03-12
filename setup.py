import os
from setuptools import setup, find_packages, Extension

pandora_module = Extension(
    '_pandora',
    sources = [
        'pandora/_pandora/main.c',
        'pandora/_pandora/crypt.c',
    ],
    libraries = ['fmodex',],
)

setup(
    name = "pandora",
    version = "1.0",
    author = "Andrew Moffat",
    author_email = "andrew.moffat@medtelligent.com",
    url = "",

    packages = find_packages('.'),
    package_dir = {'':'.'},
    data_files=[('.', ['README','MANIFEST.in']),],
    package_data = {
        'pandora':
        ['templates/*.xml',],
    },
    include_package_data=True,

    keywords = "pandora api",
    description = "pandora client",
    install_requires=[
        'eventlet'
    ],
    classifiers = [
        "Intended Audience :: Developers",
        'Programming Language :: Python',
    ],
    ext_modules = [pandora_module]
)
