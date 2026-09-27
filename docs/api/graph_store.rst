Graph store interface
======================

:mod:`cvcdocdb.graph_store` defines :class:`~cvcdocdb.graph_store.GraphStore`,
the abstract interface both :mod:`cvcdocdb.networkx_graph` and
:mod:`cvcdocdb.neo4j_graph` implement. Code written against this interface
(insert/update/delete, FK validation, cascade-delete strategies, bulk import,
schema introspection, vector indexing) works unchanged against either
backend.

.. note::
   ``Neo4jGraph`` does not currently subclass ``GraphStore`` directly (it
   duck-types the same interface) — see the abstract methods below for the
   full contract either backend is expected to satisfy.

See also :mod:`cvcdocdb.networkx_graph` and :mod:`cvcdocdb.neo4j_graph` for
the concrete backends, and :mod:`cvcdocdb.migration` for a generic tool that
copies an entire graph from one ``GraphStore`` implementation to another
using only this interface.

.. automodule:: cvcdocdb.graph_store
   :members:
   :member-order: bysource
   :show-inheritance:
