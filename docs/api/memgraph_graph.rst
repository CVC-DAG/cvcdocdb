Memgraph backend
================

:mod:`cvcdocdb.memgraph_graph` stores graphs in Memgraph. It reuses the
Neo4j backend's logic (same API, same change-propagation policy on insert,
update and delete) and overrides only the Cypher that Memgraph doesn't
support.

.. automodule:: cvcdocdb.memgraph_graph
   :members:
   :member-order: bysource
   :show-inheritance:
