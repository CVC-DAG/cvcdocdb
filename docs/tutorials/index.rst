Tutorials and Examples
======================

This section contains hands-on notebooks rendered directly in the docs. From
every notebook page you can also open the notebook in Google Colab, Kaggle,
or a local Jupyter server.

Tutorials and examples for the core of cvcdocdb. They use only the common
``GraphStore`` API, so they work unchanged on every backend (``NetworkXGraph``,
``Neo4jGraph``, ``MemgraphGraph``, ``JenaGraph``): most use ``NetworkXGraph``
because it needs no server. Tutorials for the optional modules live with each module in
:doc:`../optional/index`.

Getting Started
---------------

Start here for a minimal end-to-end graph workflow:

.. toctree::
   :maxdepth: 1

   notebooks/getting_started/intro_basics
   notebooks/getting_started/querying_and_filtering

Core concepts
-------------

WeakNode hierarchies and deletion strategies:

.. toctree::
   :maxdepth: 1

   notebooks/interactive/weaknodes
   notebooks/demos/delete_strategies

Dataset Examples
----------------

Each dataset notebook loads the same data into ``NetworkXGraph`` and
``Neo4jGraph`` so you can compare them. The loaders in ``cvcdocdb.exemples``
accept any backend, ``MemgraphGraph`` and ``JenaGraph`` included (the ``neo4j_*``/``networkx_*``
module names are historical).

.. toctree::
   :maxdepth: 1

   notebooks/datasets/karate_club
   notebooks/datasets/bibliography_openalex
   notebooks/datasets/movies
   notebooks/datasets/game_of_thrones

- **Karate Club** -- Zachary's karate club social network (34 members).
- **Bibliographic references** -- OpenAlex papers, authors and citations
  (bundled offline sample if network access is limited).
- **Movies** -- movies, genres and ``IN_GENRE`` relations (offline fallback).
- **Game of Thrones** -- characters and houses (offline fallback).

Backend-specific examples
-------------------------

Examples of features or workflows that are specific to one backend.

NetworkX
~~~~~~~~

Vector (ANN) indexes are only available on ``NetworkXGraph``
(``pip install "cvcdocdb[vector]"``):

.. toctree::
   :maxdepth: 1

   notebooks/demos/vector_search

Neo4j
~~~~~

Full propagation workflow on a real Neo4j database: load data, generate the
schema and entity classes, run ``init_propagation()`` and query. The same
workflow as a script: ``python -m cvcdocdb.exemples.demo_propagation``.

.. toctree::
   :maxdepth: 1

   notebooks/demos/propagation_demo

Memgraph
~~~~~~~~

There is no Memgraph-specific example: ``MemgraphGraph`` has the same API and
behaviour as ``Neo4jGraph``, so every general tutorial runs on it by creating
the graph with ``MemgraphGraph(url, user, password)`` instead.

Apache Jena
~~~~~~~~~~~

There is no Jena-specific notebook either: every general tutorial runs on
``JenaGraph("http://localhost:3030/ds")``. On top of that, the data can be
queried with SPARQL (see :doc:`../api/jena_graph`) and with
:doc:`../optional/text2sparql`.

Example scripts (``cvcdocdb.exemples``)
---------------------------------------

- Dataset loaders, for any backend: ``load_karate_club``,
  ``load_bibliografia_openalex``, ``load_movies_sample``,
  ``load_got_characters`` and (for :doc:`../optional/rico_entities`)
  ``load_ric_o_naf``.
- Command-line loader: ``python -m cvcdocdb.exemples --dataset karate --backend networkx``.
- Neo4j only: ``python -m cvcdocdb.exemples.demo_propagation``.

Notes
-----

- Notebook pages keep the original ``.ipynb`` download link in the rendered output.
- Tutorials are configured to use the ``cvcdocdb`` Jupyter kernel by default.
- Each tutorial notebook starts with an installation cell that upgrades DRM
  from GitHub and falls back to a local editable install when needed.
- Button links can be customized with env vars: ``DRM_DOCS_GITHUB_REPO``,
  ``DRM_DOCS_GITHUB_REF``, ``DRM_DOCS_LOCAL_JUPYTER_BASE``, and
  ``DRM_DOCS_LOCAL_NOTEBOOK_PREFIX``.
