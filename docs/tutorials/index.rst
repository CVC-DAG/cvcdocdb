Tutorials and Examples
======================

This section contains hands-on notebooks rendered directly in the docs.

Each dataset notebook shows how to load the same data into both
``NetworkXGraph`` and ``Neo4jGraph``. From every notebook page you can also
open the notebook in Google Colab, Kaggle, or a local Jupyter server.

Getting Started
---------------

Start here for a minimal end-to-end graph workflow:

.. toctree::
   :maxdepth: 1

   notebooks/getting_started/intro_basics
   notebooks/getting_started/querying_and_filtering

Demos
-----

Interactive explorations with widgets, vector search, and advanced features:

.. toctree::
   :maxdepth: 1

   notebooks/interactive/weaknodes
   notebooks/demos/vector_search
   notebooks/demos/delete_strategies
   notebooks/demos/propagation_demo
   notebooks/demos/ric_o_demo
   notebooks/demos/ric_o_networkx_demo

Dataset Examples
----------------

The tutorials below are grouped by dataset so you can compare how the same
graph loads in the in-memory backend and in Neo4j.

Karate Club
~~~~~~~~~~~

Classic social network benchmark from Zachary's karate club study. It has 34
members and is commonly used for community detection examples.

.. toctree::
   :maxdepth: 1

   notebooks/datasets/karate_club

Bibliographic references
~~~~~~~~~~~~~~~~~~~~~~~~

OpenAlex-based example with paper, author, and citation relationships. If
network access is limited, the notebook falls back to a bundled sample so the
example still runs.

.. toctree::
   :maxdepth: 1

   notebooks/datasets/bibliography_openalex

PyTorch / PyTorch Geometric dataloader
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Streams the bibliographic dataset through ``cvcdocdb.torch_dataloader``
(``GraphDataset``, ``SubgraphDataset``), trains node embeddings with
``MetaPath2Vec`` on the heterogeneous Author/Paper graph, and runs a small
link-prediction example.

.. toctree::
   :maxdepth: 1

   notebooks/datasets/torch_dataloader_bibliography

Movies
~~~~~~

Small movie-domain graph with movies, genres, and ``IN_GENRE`` relations.
The loader uses a public sample API and ships with an offline fallback.

.. toctree::
   :maxdepth: 1

   notebooks/datasets/movies

Game of Thrones
~~~~~~~~~~~~~~~

Character/house graph built from a public Thrones API. It also includes an
offline fallback so the example remains usable without network access.

.. toctree::
   :maxdepth: 1

   notebooks/datasets/game_of_thrones

Generating classes from OWL/RDF
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Generate Python entity classes from an RDF/OWL ontology (e.g. RiC-O).
The pipeline downloads the ontology, converts it to DRM YAML, and generates
``Node``, ``WeakNode``, ``Relation``, and ``WeakRelation`` subclasses.

.. toctree::
   :maxdepth: 1

   notebooks/datasets/generating_classes_from_owl

Notes
-----

- Notebook pages keep the original ``.ipynb`` download link in the rendered output.
- Tutorials are configured to use the ``cvcdocdb`` Jupyter kernel by default.
- Each tutorial notebook starts with an installation cell that upgrades DRM
  from GitHub and falls back to a local editable install when needed.
- Button links can be customized with env vars: ``DRM_DOCS_GITHUB_REPO``,
  ``DRM_DOCS_GITHUB_REF``, ``DRM_DOCS_LOCAL_JUPYTER_BASE``, and
  ``DRM_DOCS_LOCAL_NOTEBOOK_PREFIX``.
