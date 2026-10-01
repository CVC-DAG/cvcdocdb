Neo4j Enterprise features
=========================

cvcdocdb targets **Neo4j Community Edition**, and that is the default mode of
:class:`~cvcdocdb.neo4j_graph.Neo4jGraph`. The methods below are only enabled
with ``Neo4jGraph(..., edition="enterprise")``.

.. warning::

   They require a valid Neo4j Enterprise license and are **not fully
   tested**: CI runs on Neo4j Community only.

.. automodule:: cvcdocdb.neo4j_enterprise
   :members:
   :member-order: bysource
   :show-inheritance:
