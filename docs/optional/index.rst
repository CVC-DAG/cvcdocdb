Optional modules
================

The core of cvcdocdb (:doc:`../api/base`, :doc:`../api/graph_store`,
:doc:`../api/migration` and the backends) is documented in the general part.
The modules below build on it but are **optional**: ``import cvcdocdb``
doesn't import them, and some need an extra (``pip install "cvcdocdb[extra]"``).
Each page has its own installation notes, examples, tutorials and API.

.. list-table::
   :header-rows: 1
   :widths: 25 25 50

   * - Module
     - Install
     - What for
   * - :doc:`drm_entities`
     - (none)
     - DRM semantic entities: people, places, cultural documents, layouts…
   * - :doc:`rico_entities`
     - (none)
     - RiC-O archival entities generated from the ontology
   * - :doc:`rdf_schema`
     - ``[rdf]``
     - Convert RDF/OWL ontologies to a YAML schema and entity classes
   * - :doc:`schema_gen`
     - ``[schema]``
     - Generate entity classes from a YAML schema
   * - :doc:`torch_dataloader`
     - ``[torch]``
     - Stream a graph into PyTorch / PyTorch Geometric
   * - :doc:`text2cypher`
     - ``[graphrag]`` on Neo4j only
     - Natural-language questions translated to Cypher by an LLM

.. toctree::
   :maxdepth: 1

   drm_entities
   rico_entities
   rdf_schema
   schema_gen
   torch_dataloader
   text2cypher
