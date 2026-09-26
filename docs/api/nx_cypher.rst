Cypher on NetworkXGraph
=======================

:mod:`cvcdocdb.nx_cypher` is the engine behind
``NetworkXGraph.query(cypher)`` for read-only queries: it parses the query
and evaluates it against the in-memory graph with Neo4j semantics, so the
same Cypher gives the same results on ``NetworkXGraph`` and ``Neo4jGraph``.
Syntax outside the supported subset raises ``ValueError`` instead of
returning wrong results. Write queries (``CREATE``, ``MERGE``, ``SET``,
``DELETE``) are handled separately by ``NetworkXGraph``.

.. automodule:: cvcdocdb.nx_cypher
   :members: execute_read, is_write_query, NxCypherError
