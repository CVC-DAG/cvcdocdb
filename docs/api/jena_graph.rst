Apache Jena backend (SPARQL)
============================

:mod:`cvcdocdb.jena_graph` stores graphs as RDF 1.2 in Apache Jena Fuseki (or
another SPARQL 1.2 store, see below), with the same API and behaviour as every other
backend. :mod:`cvcdocdb.jena_rdf` defines the RDF layout, so the data can also
be queried directly with SPARQL.

.. code-block:: python

    from cvcdocdb import JenaGraph, Node

    graph = JenaGraph("http://localhost:3030/ds")      # a Fuseki dataset
    graph.insertNode(Node(pk={"doc": "D1"}, main_label="Document", title="Padró"))
    graph.query(
        "PREFIX label: <urn:cvcdocdb:label/> PREFIX prop: <urn:cvcdocdb:prop/> "
        "SELECT ?t WHERE { ?d a label:Document ; prop:title ?t }"
    )  # [{'t': 'Padró'}]

How it works:

- It reuses the ``NetworkXGraph`` logic (identical to ``Neo4jGraph``'s: the
  shared propagation scenarios check it) on an in-memory copy of the graph,
  so the graph has to fit in RAM.
- Every mutating call, or a whole ``batch()``, is sent as **one atomic SPARQL
  Update** with only what changed. If it fails, nothing is sent and the copy
  is rolled back.
- A version stamp detects writes from other clients. The copy is refreshed
  before each write. If another client writes in the middle of a call or
  batch, nothing is applied and
  :class:`~cvcdocdb.jena_graph.ConcurrentModificationError` is raised.
- ``query()`` runs SPARQL strings on the server (SPARQL Update is refused:
  writes go through the API so the propagation policy applies). Dict
  filters and Cypher run on the in-memory copy, as on ``NetworkXGraph``.
- ``namespace`` (default ``urn:cvcdocdb:``) prefixes every IRI it writes.
  Triples outside it are never touched, and ``graph_iri`` stores the data in
  a named graph. ``clear()`` deletes everything in the namespace.
- Vector indexes are not supported (as on Neo4j).

**Requirement: Apache Jena Fuseki >= 6.2.0** (RDF 1.2 triple terms and
annotations). ``JenaGraph`` reads the version from Fuseki's ``/$/server``
endpoint on connection, before reading any data, and raises
:class:`~cvcdocdb.jena_graph.FusekiVersionError` if it is older or can't be
determined. Pass ``server_url=`` if Fuseki is behind a proxy, or
``check_fuseki_version=False`` to use another SPARQL 1.2 store at your own
risk. The version is available as ``graph.server_version``.

Natural-language questions:
:doc:`../optional/text2sparql` or :doc:`../optional/text2query`.

.. automodule:: cvcdocdb.jena_graph
   :members:
   :member-order: bysource
   :show-inheritance:
   :exclude-members: __weakref__

RDF layout
----------

.. automodule:: cvcdocdb.jena_rdf
   :members: RdfMapping
   :member-order: bysource
