Natural-language queries on any backend (Text2Query)
====================================================

.. admonition:: Optional module

   - **Install:** as :doc:`text2cypher` (``[graphrag]`` only on ``Neo4jGraph``).
   - **Import:** ``from cvcdocdb import Text2Query``

:class:`~cvcdocdb.text2query.Text2Query` picks the translator from the
backend, so the same script works on every backend:

.. list-table::
   :header-rows: 1

   * - Backend
     - Translator
     - ``result.language``
   * - ``JenaGraph``
     - :doc:`text2sparql`
     - ``"sparql"``
   * - ``NetworkXGraph``, ``Neo4jGraph``, ``MemgraphGraph``
     - :doc:`text2cypher`
     - ``"cypher"``

.. code-block:: python

    from cvcdocdb import Text2Query, Text2QueryError

    t2q = Text2Query(graph, llm=my_llm)
    try:
        result = t2q.query("Quantes pàgines té el document D1?")
        print(result.language, result.query, result.records)
    except Text2QueryError as exc:   # base of Text2CypherError / Text2SPARQLError
        print("Could not answer:", exc)

API
---

.. automodule:: cvcdocdb.text2query
   :no-members:

.. autoclass:: cvcdocdb.text2query.Text2Query
   :members: query
   :no-undoc-members:

.. autoclass:: cvcdocdb.text2query.Text2QueryResult
   :no-members:

.. autoexception:: cvcdocdb.text2query.Text2QueryError
   :no-members:
