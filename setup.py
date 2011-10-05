import os
from os.path import join, abspath, dirname, exists, split
from setuptools import setup, find_packages, Extension
import sys
import glob
import re


THIS_DIR = abspath(dirname(__file__))
SRC_DIR = join(THIS_DIR, "src")
PKG_DIR = join(SRC_DIR, "pypandora")


python_include = "python%d.%d" % (sys.version_info.major, sys.version_info.minor)


# find fmod...
fmod_lib = glob.glob("/usr/local/lib/libfmod*")
if not fmod_lib: fmod_lib = glob.glob("/usr/lib/libfmod*")
if not fmod_lib: raise Exception, "please install fmod http://www.fmod.org/index.php/download"

fmod_lib = fmod_lib[0]
fmod_lib = split(fmod_lib)[1]
fmod_lib = re.sub("^lib(.+?)\.so$", "\\1", fmod_lib)




pandora_module = Extension(
    '_pandora',
    sources = [
        "src/pypandora/_pandora/main.c",
        "src/pypandora/_pandora/crypt.c",
    ],
    include_dirs=[python_include],
    libraries = [fmod_lib],
)

setup(
    name = "pypandora",
    version = "1.0",
    author = "Andrew Moffat",
    author_email = "andrew.robert.moffat@gmail.com",
    url = "https://github.com/amoffat/pypandora",

    packages = find_packages("src"),
    package_dir = {"":"src"},
    package_data = {
        "": [
            "templates/*.xml",
            "fmod/*.so"
        ],
    },
    include_package_data=True,
    exclude_package_data={
        "": ["_pandora/*"]
    },

    keywords = "pandora api",
    description = "pandora client",
    install_requires=[
        'eventlet'
    ],
    classifiers = [
        "Intended Audience :: Developers",
        'Programming Language :: Python',
    ],
    ext_package="pypandora",
    ext_modules = [pandora_module]
)