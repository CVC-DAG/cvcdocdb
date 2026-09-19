from setuptools import setup, find_packages
import os

NAME = "cvcdocdb"
VERSION = "1.1.0a3"
DESCR = "Graph-based document representation library with Neo4j and NetworkX backends"
URL = "https://github.com/CVC-DAG/cvcdocdb"
AUTHOR = "Oriol Ramos Terrades"
EMAIL = "oriolrt@cvc.uab.cat"
LICENSE = "GPL-3.0-or-later"

PACKAGES = find_packages(exclude=["test", "test.*"])


def get_long_description():
    path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "README.rst")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    return ""


def get_requirements():
    reqs = []
    path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "requirements.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                # Skip empty lines and lines that are not actual package names (e.g. -rtd-theme)
                if line and not line.startswith("-") and not line.startswith("#"):
                    reqs.append(line)
    return reqs


setup(
    name=NAME,
    version=VERSION,
    description=DESCR,
    long_description=get_long_description(),
    long_description_content_type="text/x-rst",
    author=AUTHOR,
    author_email=EMAIL,
    url=URL,
    license=LICENSE,
    packages=PACKAGES,
    package_dir={"": "."},
    install_requires=get_requirements(),
    keywords=["document representation", "knowledge graph", "neo4j", "networkx", "document analysis"],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
    ],
    project_urls={
        "Documentation": "https://cvc-dag.github.io/cvcdocdb/",
        "Source": "https://github.com/CVC-DAG/cvcdocdb",
        "Tracker": "https://github.com/CVC-DAG/cvcdocdb/issues",
    },
)