cvcdocdb Documentation
======================

cvcdocdb (Document Representation Model) is a Python library for graph-based
document representation with Neo4j, Memgraph, Apache Jena (SPARQL) and an
in-memory NetworkX backend.

The documentation has two parts: the **general** part (the core API, the
backends, and the tutorials that work on every backend) and the **optional
modules** (domain entities, ontology import, code generation, PyTorch
loaders, Text2Cypher), each with its own installation notes, examples,
tutorials and API.

.. toctree::
   :maxdepth: 1
   :caption: General

   tutorials/index
   api/base
   api/graph_store
   api/migration

.. toctree::
   :maxdepth: 1
   :caption: Backends

   api/networkx_graph
   api/nx_cypher
   api/neo4j_graph
   api/neo4j_enterprise
   api/memgraph_graph
   api/jena_graph

.. toctree::
   :maxdepth: 2
   :caption: Optional modules

   optional/index

Features
--------

- **Graph-based document representation**: Model documents as nodes and
  relations, including hierarchical structures such as Document → Section → Page.
- **Two backends**: Full Neo4j integration via ``Neo4jGraph`` (targeting
  Neo4j Community Edition; opt-in Enterprise-only features, see
  `Neo4j editions: Community (default) and Enterprise`_), a Memgraph
  backend via ``MemgraphGraph`` (same propagation policy, see
  `Memgraph backend`_), an Apache Jena (SPARQL) backend via ``JenaGraph``
  (the graph as RDF 1.2 in Fuseki, see :doc:`api/jena_graph`), or an
  in-memory ``NetworkXGraph`` (NetworkX) for testing and tutorials.
- **Two entity levels**: Root entities (``Node``) and child entities
  (``WeakNode``) with composite primary keys and cascade delete
  propagation. Inserting a WeakNode inserts all its ancestors. A chain has
  at most ``MAX_WEAK_CHAIN_DEPTH`` = 3 nodes (the root plus two levels, e.g.
  ``Document → Section → Page``). Deeper WeakNodes emit a
  ``WeakNodeDepthWarning``, and the next major version will give them an
  automatic surrogate key.
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
  ``is_weak``, ``parent_relation`` and ``_weak_init_done`` flags that enable
  cascade delete and hierarchical traversal (see `Propagation Properties`_).
- **Transactional group creation**: ``create_group()`` creates a strong node
  together with its WeakNodes and WeakRelations in a single isolated
  transaction. On failure the entire group is rolled back.
- **Lazy background initialization**: ``init_propagation()`` scans the
  backend graph, detects WeakNodes from edge structure, and initializes
  propagation properties. Supports background mode and progress callbacks.
- **Backend-to-backend migration**: ``cvcdocdb.migration.migrate()`` copies
  an entire graph — nodes, edges, propagation properties and vector indexes —
  between any two ``GraphStore`` backends (e.g. ``NetworkXGraph`` →
  ``Neo4jGraph``).

**Optional modules** (not imported by ``import cvcdocdb``; see
:doc:`optional/index`):

- **DRM semantic entities** (``cvcdocdb.drm_entities``): ``IndividuPadro``,
  ``LlocPadro``, ``Fotografia``...
- **RiC-O entities** (``cvcdocdb.rico_entities``), generated from the RiC-O ontology.
- **RDF/OWL ontology conversion** and **class generation**
  (``cvcdocdb.rdf_schema``, ``cvcdocdb.schema_gen``).
- **PyTorch / PyTorch Geometric dataloader** (``cvcdocdb.torch_dataloader``):
  streams a graph into PyG-ready tensors without loading it all into memory.
- **Natural-language queries**: ``Text2Query`` on any backend, which uses
  ``Text2SPARQL`` (``cvcdocdb.text2sparql``) on ``JenaGraph`` and
  ``Text2Cypher`` (``cvcdocdb.text2cypher``) on the others.

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
  ``init_propagation()`` also sets it on **any** edge into an ``is_weak``
  node that doesn't carry the flag yet (an explicit ``_propagate=False`` is
  kept). For example, after ``init_propagation()`` a ``CITES`` edge pointing
  at a Section makes ``deleteNode(src, propagation=True)`` delete that
  Section too.
- ``is_weak`` and ``_propagate`` (bool) on **child nodes** (WeakNodes)
  linked to their parent by a ``_propagate`` edge. Use them to find all the
  nodes that will be cascade-deleted when their parent is removed. They are
  set by ``init_propagation()`` and ``create_group()``, but not by
  ``insertNode()``, which only marks the edge.

**Operational properties** — track initialization state:

- ``parent_relation`` (str) on **child nodes**: the type of the edge linking
  the child to its parent (e.g. ``"HAS_SECTION"``, ``"HAS_PAGE"``). Set by
  ``init_propagation()`` and ``create_group()``, which also stores it on
  that edge.
- ``_weak_init_done`` (bool): whether a node has been processed by
  ``init_propagation()``, i.e. its WeakNode children are initialized. Set
  on every node the scan processes, and on the strong node by
  ``create_group()``.

The same properties are set by every backend (``NetworkXGraph``,
``Neo4jGraph`` and ``MemgraphGraph``; see ``test/test_propagation_contract.py``).

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
    # (the section node isn't marked yet)

    graph.init_propagation()
    #   section.is_weak = True              (structural: it's a WeakNode)
    #   section._propagate = True           (structural: cascade-delete enabled)
    #   section.parent_relation = "HAS_SECTION" (operational: edge type)
    #   doc._weak_init_done = True          (operational: children initialized)
    #   section._weak_init_done = True      (processed too: it has no children)

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
    section.is_weak = True       # child: "I am a WeakNode" (+ _propagate, parent_relation)
    page.is_weak = True          # child: "I am a WeakNode" (+ _propagate, parent_relation)

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

Memgraph backend
----------------

``MemgraphGraph`` stores the graph in `Memgraph <https://memgraph.com/>`_. It
subclasses ``Neo4jGraph`` (Memgraph speaks Bolt and Cypher, and uses the same
``neo4j`` driver), so the API and the **change-propagation policy** are
identical to the Neo4j backend:

- **Insert**: a WeakNode inserts its parent, and the parent→child edge
  carries ``_propagate=TRUE``. A missing parent, child keys that don't match
  the parent's, or a duplicate key are refused. Dependencies become ``Valor``
  nodes.
- **Update**: ``update=True`` merges attributes. ``replace=True`` deletes the
  old node with propagation (its WeakNode descendants too) and recreates it.
- **Delete**: RESTRICT by default, ``propagation=True`` for WeakNode
  descendants, ``detach=True`` (CASCADE), or ``on_delete="set_null"``.
- **Relations**: FK validation of both endpoints, plus ``update``/``replace``.

.. code-block:: python

    from cvcdocdb import MemgraphGraph

    graph = MemgraphGraph("bolt://localhost:7687", "", "")  # no auth by default

``test/test_memgraph_graph.py`` runs the same propagation scenarios on Neo4j
and Memgraph and checks that both leave the same graph and raise the same
errors. The only Memgraph-specific code is pk indexes (``SHOW INDEX INFO``,
``CREATE INDEX ON :Label(props)``) and label/relationship-type listing for
``schema_yaml()``. The Neo4j Enterprise features are not available on
Memgraph (``EnterpriseFeatureError``; see :doc:`api/memgraph_graph`), and neither is ``drop_constraint()``.
Tested with Memgraph 3.13 (Community).

Neo4j editions: Community (default) and Enterprise
--------------------------------------------------

CVCDocDB targets **Neo4j Community Edition**. It is the edition the test suite
and CI run on (``neo4j:5-community``), and ``Neo4jGraph`` uses it by default
(``edition="community"``). Everything described above works on both Community
and Enterprise.

A few Neo4j **Enterprise-only** features are available as an opt-in mode
(see :mod:`cvcdocdb.neo4j_enterprise`)::

    graph = Neo4jGraph(url, user, password, edition="enterprise")  # emits a UserWarning
    graph.create_node_key_constraint("Document", ["doc"])           # NODE KEY
    graph.create_property_existence_constraint("Document", "title") # IS NOT NULL
    graph.create_property_type_constraint("Document", "year", "INTEGER")  # Neo4j 5.9+
    graph.create_database("projecte1")                              # multiple databases
    graph.drop_database("projecte1")

In the default Community mode these methods raise ``EnterpriseFeatureError``
without contacting the server. In Enterprise mode, every call also checks that
the server really is Enterprise (``graph.server_edition()``).

.. warning::

   The Enterprise features require a valid **Neo4j Enterprise license** and
   are **not fully tested**. The regular suite only checks the generated
   Cypher and the Community-mode guards. The tests against a real Enterprise
   server only run when ``NEO4J_ENTERPRISE_URL`` is set, and CI does not set
   it. No constraint is created automatically. A ``NODE KEY`` on a label used
   with several pk shapes rejects the nodes of the other shapes.

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

Third-party software and licenses
---------------------------------

CVCDocDB itself is licensed under the GNU General Public License v3 or later (see ``LICENSE`` in the repository). It does **not** include or redistribute any database server. It talks to Neo4j, Memgraph and Apache Jena Fuseki over their network protocols (Bolt, HTTP/SPARQL), and each server is a separate product under its own license. Installing, running and licensing a server is the responsibility of whoever deploys it.

.. list-table::
   :header-rows: 1
   :widths: 25 30 45

   * - Software
     - Used by
     - License
   * - `Neo4j <https://neo4j.com/licensing/>`_ Community Edition
     - ``Neo4jGraph`` (default)
     - GPL-3.0
   * - Neo4j Enterprise Edition
     - ``Neo4jGraph(edition="enterprise")``
     - Commercial: needs an Enterprise license
   * - `Memgraph <https://github.com/memgraph/memgraph/tree/master/licenses>`_
     - ``MemgraphGraph``
     - Business Source License 1.1. It is source-available, not open source: production use is limited by its Additional Use Grant, and it converts to Apache-2.0 on its Change Date. Enterprise features fall under the Memgraph Enterprise License.
   * - `Apache Jena Fuseki <https://jena.apache.org/>`_
     - ``JenaGraph``
     - Apache-2.0

Python packages installed with CVCDocDB are under licenses compatible with the GPL-3.0:

* ``neo4j`` (driver): Apache-2.0
* ``networkx``, ``numpy``: BSD-3-Clause
* ``filelock``: MIT
* ``tqdm``: MPL-2.0 and MIT
* Optional extras: ``neo4j-graphrag`` and ``hnswlib`` (Apache-2.0), ``rdflib`` (BSD-3-Clause), ``pyyaml`` and ``torch_geometric`` (MIT), ``torch`` (BSD-style; see its metadata).

The Docker images used by the test suite and CI (``neo4j:5-community``,
``memgraph/memgraph``, ``secoresearch/fuseki``) are only pulled to run tests. They
are not part of the CVCDocDB package.

This section is informative, not legal advice: check each license for your
use case.

Acknowledgements
----------------

This work has been partially supported by the Spanish project
PID2024-157778OB-I00, Ministerio de Ciencia e Innovación, the Departament
de Cultura of the Generalitat de Catalunya, and the CERCA Program /
Generalitat de Catalunya. Adrià Molina is funded with the PRE2022-101575
grant provided by MCIN / AEI / 10.13039 / 501100011033 and by the
European Social Fund (FSE+).
