import os
from setuptools import setup, find_packages, Extension

pandora_module = Extension(
    '_pandora',
    sources = [
        'pypandora/_pandora/main.c',
        'pypandora/_pandora/crypt.c',
    ],
    libraries = ['fmodex',],
)

setup(
    name = "pypandora",
    version = "1.0",
    author = "Andrew Moffat",
    author_email = "andrew.moffat@medtelligent.com",
    url = "https://github.com/amoffat/pypandora",

    packages = find_packages('.'),
    package_dir = {'':'.'},
    data_files=[('.', ['README','MANIFEST.in']),],
    package_data = {
        'pypandora': ['templates/*.xml', "cues/*"],
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
