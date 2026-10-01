Neo4j backend
=============

:mod:`cvcdocdb.neo4j_graph` exposes the persistent backend that stores graphs in
Neo4j. It supports the same core concepts as the in-memory backend while
adding database connectivity, foreign-key checks, and graph persistence.

It targets **Neo4j Community Edition** (the default, ``edition="community"``).
Enterprise-only features are opt-in with ``edition="enterprise"``. They
require an Enterprise license and are not fully tested. See
:doc:`neo4j_enterprise`.

.. automodule:: cvcdocdb.neo4j_graph
   :members:
   :member-order: bysource
   :show-inheritance:
