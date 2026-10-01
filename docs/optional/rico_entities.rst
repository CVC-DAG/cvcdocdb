RiC-O entities
==============

.. admonition:: Optional module

   - **Install:** included in ``pip install cvcdocdb`` (no extra dependencies).
   - **Import:** ``from cvcdocdb.rico_entities import Thing, Agent, RecordResource, ...``
     — ``import cvcdocdb`` does **not** import this module.

:mod:`cvcdocdb.rico_entities` holds the entity classes generated from the
`RiC-O <https://www.ica.org/standards/RiC/ontology>`_ ontology (Records in
Contexts, the ICA ontology for archival description) with
:doc:`rdf_schema` and :doc:`schema_gen`: 677 ``Node``/``WeakNode``/
``Relation``/``WeakRelation`` subclasses. ``Thing`` is the single strong
(root) node, and every other entity is a ``WeakNode``.

The loader :func:`cvcdocdb.exemples.load_ric_o_naf.load_ric_o_naf` fills a
graph (any backend) with RiC-O data from the French National Archives.

Tutorials
---------

The same RiC-O model and loader on each backend:

.. list-table::
   :header-rows: 1

   * - Backend
     - Tutorial
   * - ``Neo4jGraph``
     - :doc:`../tutorials/notebooks/demos/ric_o_demo`
   * - ``NetworkXGraph``
     - :doc:`../tutorials/notebooks/demos/ric_o_networkx_demo`

.. toctree::
   :hidden:

   ../tutorials/notebooks/demos/ric_o_demo
   ../tutorials/notebooks/demos/ric_o_networkx_demo

API
---

The classes are generated and have no methods of their own beyond their
constructors, so they aren't listed one by one here. See the module source
or regenerate it as shown in :doc:`rdf_schema`.

.. automodule:: cvcdocdb.rico_entities
   :no-members:
