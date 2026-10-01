DRM semantic entities
=====================

.. admonition:: Optional module

   - **Install:** included in ``pip install cvcdocdb`` (no extra dependencies).
   - **Import:** ``from cvcdocdb.drm_entities import IndividuPadro, LlocPadro, ...`` — ``import cvcdocdb`` does **not** import this module.
   - ``from cvcdocdb import IndividuPadro`` still works but emits a
     ``DeprecationWarning``; it will be removed in the next major version.


The :mod:`cvcdocdb.drm_entities` module contains the domain-specific node types
of the Document Representation Model (people, places, cultural documents,
layouts...) built on top of :mod:`cvcdocdb.base`. These classes add
validation, default labels, and automatic materialisation of properties into
graph structures.

``Atribut`` (the ``Valor`` node that materialises ``be_value_properties``) is
part of the core and lives in :mod:`cvcdocdb.base`; it is re-exported here.

Example
-------

.. code-block:: python

    from cvcdocdb import NetworkXGraph
    from cvcdocdb.drm_entities import IndividuPadro

    graph = NetworkXGraph()
    person = IndividuPadro(pk=123, nom="Joan", cognom1="Miró", cognom2="Ferrer")
    graph.insertNode(person)   # nom/cognom1/cognom2 become Valor nodes
    graph.close()

The entities work the same on every backend (``NetworkXGraph``, ``Neo4jGraph``,
``MemgraphGraph``).

API
---

.. automodule:: cvcdocdb.drm_entities
   :members:
   :exclude-members: document_class
   :member-order: bysource
   :show-inheritance:
