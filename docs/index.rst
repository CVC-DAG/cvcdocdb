cvcdocdb Documentation
======================

cvcdocdb (Document Representation Model) is a Python library for graph-based
document representation with Neo4j and an in-memory NetworkX backend.

.. toctree::
   :maxdepth: 1
   :caption: Contents:

   tutorials/index
   api/base
   api/drm_entities
   api/graph_store
   api/migration
   api/neo4j_graph
   api/networkx_graph
   api/rdf_schema
   api/schema_gen
   api/text2cypher
   api/torch_dataloader

Features
--------

- **Graph-based document representation**: Model documents as nodes and
  relations, including hierarchical structures such as Document → Section → Page.
- **Two backends**: Full Neo4j integration via ``Neo4jGraph`` or an
  in-memory ``NetworkXGraph`` (NetworkX) for testing and tutorials.
- **Two entity levels**: Root entities (``Node``) and child entities
  (``WeakNode``) with composite primary keys and cascade delete
  propagation.
- **Dependency auto-insertion**: String properties (for example names) are
  automatically materialised as ``Valor`` nodes when the graph backend supports them.
- **FK validation**: Foreign key constraints on relations prevent
  dangling references.
- **ON DELETE strategies**: CASCADE, RESTRICT, SET NULL.
- **Query & filtering**: Secondary index on scalar properties, multi-filter
  search with intersection/union, and debug snapshots.
- **Vector search (NetworkX only)**: HNSW-based ANN indexing on node
  properties with ``cosine``, ``l2``, and ``ip`` distance spaces.
- **Propagation properties**: Every node and edge can carry ``_propagate``,
  ``is_weak``, ``parent_relation``, and ``_dependencies`` flags that enable
  cascade delete and hierarchical traversal.
- **Transactional group creation**: ``create_group()`` creates a strong node
  together with its WeakNodes and WeakRelations in a single isolated
  transaction. On failure the entire group is rolled back.
- **Lazy background initialization**: ``init_propagation()`` scans the
  backend graph, detects WeakNodes from edge structure, and initializes
  propagation properties. Supports background mode and progress callbacks.
- **Backend-to-backend migration**: ``cvcdocdb.migration.migrate()`` copies
  an entire graph — nodes, edges, and vector indexes — between any two
  ``GraphStore`` backends (e.g. ``NetworkXGraph`` → ``Neo4jGraph``).
- **PyTorch / PyTorch Geometric dataloader**: ``cvcdocdb.torch_dataloader``
  streams a graph into PyG-ready tensors for node embedding models
  (``MetaPath2Vec``) and link prediction, without loading the whole graph
  into memory.

Primary Key
-----------

Every node must have a primary key (``pk``) or a ``neo4j_id`` (or both).
The ``pk`` is either an ``int`` or a ``dict``:

.. code-block:: python

    # Integer PK — converted to {"id": value}
    node = Node(pk=42, main_label="Document")

    # Dict PK — used as-is
    node = Node(pk={"doc_id": "DOC-001", "version": 2}, main_label="Document")

    # Auto-assigned PK — pass None explicitly; the backend generates an ID
    node = Node(pk=None, main_label="AutoIdNode")
    graph.insertNode(node)  # node._primary_key becomes {"id": <generated_id>}

    # Reconstructed from DB — neo4j_id is used as PK
    node = Node(neo4j_id=123, main_label="Document")  # _primary_key = {"id": 123}

**Rules:**

- ``Node(pk={"id": 1}, main_label="X")`` → ``_primary_key = {"id": 1}``
- ``Node(pk=42, main_label="X")`` → ``_primary_key = {"id": 42}``
- ``Node(pk=None, main_label="X")`` → ``_primary_key = None`` (backend assigns after insert)
- ``Node(pk=None, neo4j_id=123, main_label="X")`` → ``_primary_key = {"id": 123}``
- ``Node(main_label="X")`` → **ValueError** — pk must be provided

A node with ``pk=None`` is valid — the backend (Neo4j or NetworkX) assigns
an auto-generated ID as the primary key after insertion. If no backend is
used, ``_primary_key`` remains ``None``.

Query & Filtering
-----------------

``NetworkXGraph`` maintains a secondary index on scalar properties, enabling
fast lookups without scanning all nodes:

.. code-block:: python

    from cvcdocdb import NetworkXGraph, Node

    graph = NetworkXGraph()
    alice = Node(pk={"name": "Alice"}, main_label="Author")
    alice["affiliation"] = "MIT"
    bob = Node(pk={"name": "Bob"}, main_label="Author")
    bob["affiliation"] = "Stanford"
    graph.insertNode(alice)
    graph.insertNode(bob)

    # Find all authors from MIT
    mit_authors = graph.find_nodes_by_property("affiliation", "MIT")
    print(mit_authors)  # [node_id_of_alice]

    # Multi-filter search (intersection or union)
    results = graph.find_nodes({"affiliation": "MIT"}, match="all")

    # Debug snapshot
    graph.print_debug()

Vector Indexing
---------------

The package exposes a common vector-index API on graph stores:

- ``enable_vector_index(property_name, dimensions, space="cosine", ...)``
- ``query_vector_index(property_name, vector, top_k=10)``

Current backend support:

- ``NetworkXGraph``: supported (uses ``hnswlib``)
- ``Neo4jGraph``: API is present but currently raises ``NotImplementedError``

Example (``NetworkXGraph``):

.. code-block:: python

    from cvcdocdb import NetworkXGraph, Node

    graph = NetworkXGraph()
    graph.enable_vector_index("embedding", dimensions=3, space="cosine")

    graph.insertNode(Node(pk={"id": 1}, main_label="Doc", embedding=[1.0, 0.0, 0.0]), replace=True)
    graph.insertNode(Node(pk={"id": 2}, main_label="Doc", embedding=[0.0, 1.0, 0.0]), replace=True)

    results = graph.query_vector_index("embedding", [1.0, 0.0, 0.0], top_k=2)
    print(results)  # [(node_id, distance), ...]
    graph.close()

Propagation Properties
----------------------

Every node and edge in the graph can carry propagation-related properties.
These properties come in two families: **structural** (describing the
hierarchy) and **operational** (tracking initialization state).

**Structural properties** — describe the parent-child relationship:

- ``_propagate`` (bool): marks **edges** that trigger cascade delete when the
  parent node is removed. Set automatically on WeakNode parent-child edges.
- ``is_weak`` (bool): marks **child nodes** (WeakNodes) that are linked to
  their parent by a ``_propagate`` edge. Use this to find all nodes that
  will be cascade-deleted when their parent is removed.

**Operational properties** — track initialization state:

- ``parent_relation`` (str): stores the edge type linking a parent to its
  WeakNode child (e.g. ``"HAS_SECTION"``, ``"HAS_PAGE"``).
- ``_dependencies`` (dict): tracks auto-inserted ``Valor`` nodes for string
  properties.
- ``_weak_init_done`` (bool): tracks whether a **strong node** (parent) has
  been processed by ``init_propagation()``. Set automatically by
  ``create_group()`` because the edges already carry ``_propagate=True``.

Example:

.. code-block:: python

    from cvcdocdb import Neo4jGraph, Node, WeakNode

    graph = Neo4jGraph(
        url="bolt://localhost:7687",
        user="neo4j",
        password="secret",
        database="mydb",
    )

    doc = Node(pk={"doc_id": "DOC-1"}, main_label="Document")
    graph.insertNode(doc)

    section = WeakNode(
        parent=doc,
        parent_relation="HAS_SECTION",
        pk={"section": "intro"},
        main_label="Section",
    )
    graph.insertNode(section, insert_parent=True)

    # The edge doc → section carries _propagate=True
    # The section node carries is_weak=True

    # After init_propagation():
    #   section.is_weak = True              (structural: it's a WeakNode)
    #   section._propagate = True           (structural: cascade-delete enabled)
    #   section.parent_relation = "HAS_SECTION" (operational: edge type)
    #   doc._weak_init_done = True          (operational: parent initialized)

**Key distinction** — ``is_weak`` vs ``_weak_init_done``:

- **``is_weak``** lives on the **child node** and describes its role in the
  hierarchy. It is set by ``init_propagation()`` when it detects a node
  connected via a ``_propagate`` edge.
- **``_weak_init_done``** lives on the **parent node** and tracks whether
  that parent's WeakNodes have been processed. It is set automatically by
  ``create_group()`` because the group is created with edges that already
  carry ``_propagate=True``.

.. code-block:: python

    # After create_group(doc, [section, page]):
    doc._weak_init_done = True   # parent: "my children are initialized"
    section.is_weak = True       # child: "I am a WeakNode"
    page.is_weak = True          # child: "I am a WeakNode"

    # After init_propagation() on a pre-existing graph:
    section.is_weak = True       # child: "I was detected as a WeakNode"
    doc._weak_init_done = True   # parent: "I have been processed"

Transactional Group Creation
----------------------------

``create_group()`` inserts a strong node together with its WeakNodes and
WeakRelations in a single atomic transaction. On failure the entire group
is rolled back so the graph is never left in a partially-created state.

Example:

.. code-block:: python

    from cvcdocdb import Neo4jGraph, Node, WeakNode

    graph = Neo4jGraph(
        url="bolt://localhost:7687",
        user="neo4j",
        password="secret",
        database="mydb",
    )

    doc = Node(pk={"doc_id": "DOC-1"}, main_label="Document", title="My Document")
    section = WeakNode(
        parent=doc,
        parent_relation="HAS_SECTION",
        pk={"section": "intro"},
        main_label="Section",
        title="Introduction",
    )
    page = WeakNode(
        parent=section,
        parent_relation="HAS_PAGE",
        pk={"page": 1},
        main_label="Page",
        content="Welcome to the document.",
    )

    # Atomic creation — all-or-nothing
    doc_id = graph.create_group(
        strong_node=doc,
        weak_nodes=[section, page],
    )
    print(f"Created group with strong node id: {doc_id}")

    # _weak_init_done is automatically set on the strong node
    # because the edges already carry _propagate=True

Lazy Background Initialization
------------------------------

``init_propagation()`` scans the backend graph and initializes propagation
properties on nodes and edges that are missing them. It detects WeakNodes
by looking for edges with ``_propagate=True`` and marks the child nodes
accordingly.

Example:

.. code-block:: python

    from cvcdocdb import Neo4jGraph

    graph = Neo4jGraph(
        url="bolt://localhost:7687",
        user="neo4j",
        password="secret",
        database="mydb",
    )

    # Synchronous — blocks until complete
    result = graph.init_propagation()
    print(f"Initialized: {result}")  # True on first call, False if already done

    # Background mode — returns immediately, runs in daemon thread
    graph.init_propagation(background=True)

    # With progress callback
    def progress(processed, total):
        print(f"  {processed}/{total} nodes processed")

    graph.init_propagation(progress_callback=progress)

    # Idempotent — second call returns False immediately
    graph.init_propagation()  # Returns False

Concurrency & Persistence (NetworkXGraph)
-----------------------------------------

``NetworkXGraph`` loads the whole persisted graph into memory once per
instance and writes the whole graph back to disk on every mutation. Two
instances (in one process or several) pointing at the same
``persistence_path`` therefore each hold an independent in-memory copy of
the graph.

Every mutating method (``insertNode``, ``insertRelation``, ``deleteNode``,
``create_group``, ``init_propagation``, ``enable_vector_index``) guards its
load-mutate-save cycle with a cross-process file lock (via the ``filelock``
package): before mutating, it reloads the latest on-disk state under the
lock, so the mutation is applied on top of whatever another process already
persisted rather than a stale snapshot. This prevents the classic "two
writers" lost-update race, where whichever instance saves last would
otherwise silently erase the other's already-persisted changes.

Nested calls made by a single top-level mutation (for example
``create_group()`` calling ``insertNode()``/``insertRelation()``
internally, or ``deleteNode()``'s cascade-delete recursion) detect that the
lock is already held and skip the reload/save step, so the whole operation
still reaches disk as one atomic write — and if it raises partway through,
nothing is written at all, rather than a partially-applied change.

.. note::
   This makes concurrent access from independent processes/instances
   *safe* (no silent data loss), but each mutating call is still fully
   serialized process-wide through a single lock file — there is no
   fine-grained (per-node/per-edge) concurrency. ``NetworkXGraph`` remains
   an in-memory backend intended for testing and tutorials; for real
   concurrent production workloads, use ``Neo4jGraph``, which has proper
   ACID transactions.

Installation
------------

Install the package in development mode:

.. code-block:: bash

    pip install -e .

Quick Start
-----------

.. code-block:: python

    from cvcdocdb import Neo4jGraph, Node, WeakNode

    # Connect to Neo4j
    graph = Neo4jGraph(
        url="bolt://localhost:7687",
        user="neo4j",
        password="secret",
        database="mydb",
    )

    # Create a document hierarchy
    doc = Node(pk={"doc": "DOC-001"}, main_label="Document")
    graph.insertNode(doc, replace=True)

    section = WeakNode(parent=doc, pk={"section": 1}, main_label="Section")
    graph.insertNode(section, insert_parent=True)

    page = WeakNode(parent=section, pk={"page": 1}, main_label="Page")
    graph.insertNode(page, insert_parent=True)

    graph.close()

For the in-memory backend:

.. code-block:: python

    from cvcdocdb import NetworkXGraph, Node

    graph = NetworkXGraph()
    doc = Node(pk={"doc": "DOC-001"}, main_label="Document")
    graph.insertNode(doc)
    print(graph.get_node_ids())  # [1]
    graph.close()

Configuration
-------------

DRM uses environment variables for Neo4j connections. Multiple targets are
supported via the ``NEO4J_TARGET`` selector:

.. code-block:: bash

    export NEO4J_DEV_URL=bolt://dev-host:7687
    export NEO4J_DEV_USER=neo4j
    export NEO4J_DEV_PASSWORD=your_dev_password
    export NEO4J_DEV_DATABASE=neo4j

For a custom target:

.. code-block:: bash

    export NEO4J_TARGET=LOCAL
    export NEO4J_LOCAL_URL=bolt://localhost:7687
    export NEO4J_LOCAL_USER=neo4j
    export NEO4J_LOCAL_PASSWORD=your_password
    export NEO4J_LOCAL_DATABASE=neo4j

The real Neo4j tests default to ``DEV`` when ``NEO4J_TARGET`` is not set,
and fall back to plain ``NEO4J_*`` variables when prefixed variables are absent.

Running Tests
-------------

Run the test suite with pytest:

.. code-block:: bash

    python -m pytest test/ -v

Tests use ``NetworkXGraph`` for all unit tests and ``Neo4jGraph`` for
integration tests against a real Neo4j instance (controlled by ``NEO4J_TARGET``).

Building Documentation
----------------------

Generate the HTML documentation with Sphinx:

.. code-block:: bash

    cd docs
    sphinx-build -b html . _build/html

Or use the virtual environment:

.. code-block:: bash

    .venv/bin/sphinx-build -b html docs/ docs/_build/html/

Authors
-------

- Oriol Ramos Terrades
- Jialuo Chen
- Adrià Molina

Acknowledgements
----------------

This work has been partially supported by the Spanish project
PID2024-157778OB-I00, Ministerio de Ciencia e Innovación, the Departament
de Cultura of the Generalitat de Catalunya, and the CERCA Program /
Generalitat de Catalunya. Adrià Molina is funded with the PRE2022-101575
grant provided by MCIN / AEI / 10.13039 / 501100011033 and by the
European Social Fund (FSE+).
