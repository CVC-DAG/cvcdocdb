from setuptools import setup, find_packages
import os

NAME = "cvcdocdb"
VERSION = "0.0.0.dev0"  # real version numbers only live on `main` — see CLAUDE.md branch strategy
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


# Runtime dependencies are declared here, not read from requirements.txt:
# that file isn't shipped in the sdist (so the published wheel ended up
# declaring no dependencies at all), and it also lists development-only
# tools (sphinx, notebook...). requirements.txt is for the dev environment.
INSTALL_REQUIRES = [
    "neo4j",
    "networkx",
    "numpy",
    "filelock",
    "tqdm",
]

EXTRAS_REQUIRE = {
    # RDF/OWL ontology import (cvcdocdb.rdf_schema).
    "rdf": ["rdflib", "pyyaml"],
    # Entity-class generation from a YAML schema (cvcdocdb.schema_gen).
    "schema": ["pyyaml"],
    # Vector indexes on NetworkXGraph (enable_vector_index / query_vector_index).
    "vector": ["hnswlib"],
    # PyTorch / PyG data loaders (cvcdocdb.torch_dataloader).
    "torch": ["torch", "torch_geometric"],
    # Natural-language → Cypher (cvcdocdb.text2cypher); needs Python >= 3.10.
    "graphrag": ["neo4j-graphrag>=1.21,<2"],
}


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
    install_requires=INSTALL_REQUIRES,
    extras_require=EXTRAS_REQUIRE,
    keywords=["document representation", "knowledge graph", "neo4j", "memgraph", "networkx", "sparql", "jena", "rdf", "document analysis"],
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