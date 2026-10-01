Natural-language queries in SPARQL (Text2SPARQL)
================================================

.. admonition:: Optional module

   - **Install:** included in ``pip install cvcdocdb`` (no extra dependencies).
   - **Import:** ``from cvcdocdb import Text2SPARQL``
   - **Backend:** :class:`~cvcdocdb.jena_graph.JenaGraph`. For any backend, use
     :doc:`text2query`, which picks Text2SPARQL or Text2Cypher for you.

The SPARQL counterpart of :doc:`text2cypher`, with the same API: an LLM
translates the question to SPARQL and the query is run on the graph.

.. code-block:: python

    from cvcdocdb import JenaGraph, Text2SPARQL

    graph = JenaGraph("http://localhost:3030/ds")
    t2s = Text2SPARQL(graph, llm=lambda prompt: my_client.complete(prompt))
    result = t2s.query("Quantes pàgines té el document D1?")
    result.sparql, result.records

Notes
-----

- The schema shown to the LLM uses the RDF vocabulary
  :class:`~cvcdocdb.jena_graph.JenaGraph` writes, with ``PREFIX`` declarations
  for its namespace (``label:``, ``prop:``, ``rel:``). Relationship
  properties are read with the RDF 1.2 annotation syntax
  ``?a rel:X ?b {| prop:p ?v |}``.
- Only read-only SPARQL is executed: a ``SELECT``/``ASK``/``CONSTRUCT``/
  ``DESCRIBE`` with no update operation and no ``SERVICE`` (federated
  queries could reach other servers). Anything else raises
  :class:`~cvcdocdb.text2sparql.Text2SPARQLError` before running.
- ``schema=``, ``examples=[...]`` and ``custom_prompt=...`` work as in
  Text2Cypher.

API
---

.. automodule:: cvcdocdb.text2sparql
   :members:
   :member-order: bysource
   :exclude-members: sparql, records, metadata
