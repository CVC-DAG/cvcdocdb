# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.6.0] - 2026-10-02

### Changed

- **`JenaGraph` requires Apache Jena Fuseki >= 6.2.0** (`MIN_FUSEKI_VERSION`).
  It reads the version from Fuseki's `/$/server` endpoint on connection,
  before reading any data, and raises `FusekiVersionError` if it is older
  (these versions lack RDF 1.2) or can't be determined. The version is
  exposed as `graph.server_version`. Pass `server_url=` if Fuseki is behind
  a proxy. Behaviour change: another SPARQL 1.2 store is now refused by
  default; pass `check_fuseki_version=False` to keep using it, at your own
  risk.

- The PyPI summary now lists every backend: Neo4j, Memgraph, Apache Jena
  (SPARQL) and NetworkX.

### Fixed

- **`Neo4jGraph` no longer triggers the Neo4j driver's `PreviewWarning`**
  (driver 5.x, the one installed on Python 3.9). To filter the `id()`
  deprecation notices, cvcdocdb used the "classifications" notification API,
  which is a preview feature in driver 5.x: it warned on import and on every
  session. It now picks the stable API of each driver version: "categories"
  on 5.x, and "classifications" on 6.x, where "categories" are deprecated.
  The deprecation filter itself is unchanged.
- **`Neo4jGraph.close()` now closes the driver session** (also on
  `MemgraphGraph`). It only closed the driver, so the session stayed open
  until garbage collection, and the driver warned with a `ResourceWarning`
  ("unclosed Session") and a `DeprecationWarning` (future drivers won't close
  sessions on destruction). `close()` is now idempotent.

### Documentation

- **New "Third-party software and licenses" section** in the README and the
  docs. It explains that CVCDocDB (GPL-3.0-or-later) doesn't redistribute
  any database server, and lists the license of each backend: Neo4j
  Community GPL-3.0 and Enterprise commercial, Memgraph BSL 1.1 and MEL,
  Apache Jena Fuseki Apache-2.0. It also lists the licenses of the Python
  dependencies, and notes that the test Docker images aren't part of the
  package.

## [1.5.0] - 2026-10-01

### Added

- **Apache Jena backend: `JenaGraph`** (`cvcdocdb.jena_graph`, also
  `from cvcdocdb import JenaGraph`). It stores the graph as RDF 1.2 in
  Apache Jena Fuseki or another SPARQL 1.2 store, with the same API and
  behaviour as every other backend: it reuses the `NetworkXGraph` logic
  (identical to Neo4j's) on an in-memory copy of the graph.
  - Every mutating call, or a whole `batch()`, is sent as one atomic SPARQL
    Update with only the changes. A version stamp detects concurrent writers
    and raises `ConcurrentModificationError`; nothing is applied.
  - `query()` runs SPARQL on the server (SPARQL Update is refused), and dict
    filters and Cypher on the copy.
  - The RDF layout (`cvcdocdb.jena_rdf`) is queryable directly: label
    classes, `prop:` values, `rel:` triples with RDF 1.2 annotations for
    relationship properties. `namespace`/`graph_iri` isolate it from other
    data, and `clear()` empties it. No extra dependency (HTTP via the
    standard library, `cvcdocdb.sparql_client`).
  - The graph has to fit in RAM, and vector indexes are not supported.
  - Tested with Fuseki 6.2.0: the `GraphStore` contract suite, the
    propagation contract, the 21 shared propagation scenarios (identical
    results to NetworkX, hence to Neo4j), and migration to and from every
    other backend. CI runs a Fuseki container, and `conftest.py` starts one
    (port 3030) when none is reachable.
- **`Text2SPARQL`** (`cvcdocdb.text2sparql`): natural-language questions
  translated to SPARQL by an LLM, for `JenaGraph`, with the same API as
  `Text2Cypher`. The schema is shown in the graph's RDF vocabulary with its
  `PREFIX`es. Only read-only queries run (no update operation, no `SERVICE`).
- **`Text2Query`** (`cvcdocdb.text2query`): one natural-language API for
  every backend. It uses `Text2SPARQL` on `JenaGraph` and `Text2Cypher`
  elsewhere, and returns a `Text2QueryResult` (`query`, `language`,
  `records`). `Text2QueryError` is the new base of `Text2CypherError` and
  `Text2SPARQLError`. `text2cypher.collect_schema()` exposes the backend-
  independent schema collection.

- **`GraphStore.set_node_properties(node_id, properties)`** (NetworkX, Neo4j,
  Memgraph). Sets properties on an existing node verbatim
  (`SET n += $props`), including keys that `Node()` can't carry. Raises
  `KeyError` if the node doesn't exist.

- **Memgraph backend: `MemgraphGraph`** (`cvcdocdb.memgraph_graph`, also
  `from cvcdocdb import MemgraphGraph`). A subclass of `Neo4jGraph` (Memgraph
  speaks Bolt and Cypher), so it has the same API and the same
  change-propagation policy on insert (WeakNode parents, `_propagate` edges,
  FK and key checks, dependencies), update (`update`/`replace`) and delete
  (RESTRICT, propagation, CASCADE, SET NULL). Only pk indexes
  (`SHOW INDEX INFO`, `CREATE INDEX ON :Label(props)`) and label and
  relationship-type listing (`schema_yaml()`) use Memgraph-specific Cypher.
  Neo4j Enterprise features and `drop_constraint()` are not available.
  Tested with Memgraph 3.13 Community. `test/test_memgraph_graph.py` runs the
  `GraphStore` contract suite on Memgraph, plus 20 propagation scenarios that
  must leave the same graph and errors as on Neo4j. CI gets a Memgraph
  service, and `conftest.py` starts a Memgraph container (port 7688) when
  none is reachable.
- `Neo4jGraph` gains four internal backend hooks, with no behaviour change:
  `_existing_node_index_keys()`, `_pk_index_statement()`, `_list_labels()`
  and `_list_relationship_types()`.

- **Opt-in Neo4j Enterprise mode** (backward-compatible: the default is
  unchanged). `Neo4jGraph(..., edition="community")` is the default, and
  cvcdocdb keeps targeting and testing Neo4j Community Edition.
  `edition="enterprise"` enables Enterprise-only operations (new module
  `cvcdocdb.neo4j_enterprise`):
  `create_node_key_constraint()`, `create_property_existence_constraint()`
  (nodes or relationships), `create_property_type_constraint()` (Neo4j 5.9+),
  `create_database()` and `drop_database()`. In Community mode they raise
  `EnterpriseFeatureError` without contacting the server. In Enterprise mode,
  each call checks `server_edition()` first, and the constructor emits a
  `UserWarning`. These features require an Enterprise license and are not
  fully tested: CI runs on Community only, and the real-server tests need
  `NEO4J_ENTERPRISE_URL`. New, edition-independent helpers:
  `server_edition()` and `drop_constraint()`.

### Deprecated

- **`cvcdocdb.drm_entities` is now an optional module.** `import cvcdocdb`
  no longer imports it. Import the DRM entities from it:
  `from cvcdocdb.drm_entities import IndividuPadro`. `from cvcdocdb import
  IndividuPadro` (and `import *`) still works for every DRM entity name but
  emits a `DeprecationWarning`; the top-level names will be removed in the
  next major version. `Atribut` (the `Valor` node behind
  `be_value_properties`) is part of the core and moves to `cvcdocdb.base`;
  it's still importable from `cvcdocdb.drm_entities` and from `cvcdocdb`.

- **WeakNode chains deeper than `MAX_WEAK_CHAIN_DEPTH` = 3 nodes** (the root
  plus two levels of WeakNode). Creating a deeper WeakNode still works,
  inheriting the composite key as before, but now emits a
  `WeakNodeDepthWarning` (a `FutureWarning`, so it's shown by default). In
  the next major version such nodes will get an automatic surrogate key
  instead. `weak_chain_depth(node)` returns a node's depth.

### Fixed

- **Cypher write queries on `NetworkXGraph` were never saved.** They changed
  the graph in memory only, unlike on Neo4j. They are now saved like any
  other write. Their nodes now get `labels`/`pk`, are indexed (dict filters
  find them), and no longer get an id that a later `insertNode()` could
  reuse.

- **`Text2Cypher` didn't work on `MemgraphGraph`.** It treated it as Neo4j:
  `neo4j_graphrag`'s retriever fails on Memgraph (`CALL dbms.components()`
  without `YIELD`), and so do `db.info()` and `db.schema.*`. Memgraph now
  takes cvcdocdb's own path (write-clause check + `graph.query()`), with the
  schema read with plain Cypher. It doesn't need `cvcdocdb[graphrag]`.
- **The `Text2Cypher` schema is now the same text on every backend.**
  NetworkX ignored alternative labels, and the property order on Neo4j
  depended on `db.schema.*`. All backends now list every label, with
  labels and properties in alphabetical order.
  `test_schema_is_identical_on_every_backend` checks it on NetworkX, Neo4j
  and Memgraph, and the portable Text2Cypher tests now run on Memgraph too.
  `requirements-test.txt` now installs `neo4j-graphrag`, so CI runs the
  Neo4j Text2Cypher tests, and `conftest.py` imports it before
  `test_drm.py` mocks `neo4j`.
- **`migrate()` now copies propagation properties on nodes.** `Node()`
  reads `is_weak`/`_propagate`/`parent_relation` as structural arguments
  and drops attributes starting with `_` (`_weak_init_done`), so:
  - a NetworkX → NetworkX migration lost them;
  - a NetworkX → Neo4j/Memgraph migration of a graph after
    `init_propagation()` crashed (an `is_weak` node without a parent);
  - from Neo4j they only survived by ending up in the fallback pk.
  They are now written with `set_node_properties()` after each insert, and
  never become part of the fallback pk.
- **`migrate()` from a `MemgraphGraph` crashed** (`Explicit transaction
  already open`): the batched read path only recognised the exact class
  name `Neo4jGraph`. It now accepts any subclass.
  `test/test_migration_propagation.py` migrates a graph with every
  propagation property between all backend pairs, and checks that the
  target ends up identical and behaves the same on a propagated delete.

- **NetworkXGraph writes are now atomic, like a Neo4j transaction.** When a
  mutating call or a whole `batch()` failed, nothing was saved to disk, but
  the half-done changes stayed in memory, visible to later reads and saved
  by the next write. The in-memory state is now rolled back to the last
  saved one.
- **`init_propagation()` and `create_group()` now set the documented
  propagation properties, identically on every backend.**
  - On Neo4j/Memgraph, `init_propagation()` never wrote `parent_relation`:
    its loop iterated a result that had already been consumed. It's now set
    on the child node, as documented.
  - On NetworkX, `init_propagation()` didn't set `_weak_init_done`. It
    wrote a wrong `_dependencies` (it counted WeakNode `HAS_*` edges as
    dependencies) and skipped the property index. It now uses the same
    algorithm as Neo4j, including its last step: any edge into an `is_weak`
    node that has no `_propagate` yet gets `_propagate=True`. A propagated
    delete from the edge's source therefore removes the WeakNode, as on
    Neo4j.
  - `create_group()` now initializes its WeakNode children (`is_weak`,
    `_propagate`, `parent_relation`) like `init_propagation()` would, since
    it marks the parent `_weak_init_done`. On NetworkX the parent edge also
    gets `parent_relation`, as on Neo4j.
  - `_dependencies` is no longer part of this contract: Neo4j can't store a
    map as a property.
  `test/test_propagation_contract.py` checks the contract on NetworkX, Neo4j
  and Memgraph. The NetworkX-vs-Neo4j comparison now covers all 20 shared
  scenarios.

- **`NetworkXGraph.insertNode()` didn't insert a WeakNode's whole ancestry.**
  With `Document → Section → Page`, `insertNode(page)` created Section and Page
  but not Document (only the direct parent was inserted, without its own
  parent edge). It now inserts every ancestor recursively, each with its
  `_propagate` parent edge, as `Neo4jGraph` and `MemgraphGraph` do.
- **`NetworkXGraph` now enforces the same WeakNode checks as Neo4j, before
  writing anything.** A WeakNode whose parent is missing
  (`insert_parent=False`) now raises `CVCDocDB Exception: missing parent
  node ...` instead of a `ValueError` that left the child in the graph. A
  child whose key doesn't reference its parent's key now raises
  `RuntimeError` (Integrity Constraint Violated) instead of being accepted.
  `test/test_weaknode_hierarchy.py` runs the shared propagation scenarios
  (`test/propagation_scenarios.py`) on NetworkX and Neo4j and compares the
  results.

### Changed

- CI: GitHub Actions bumped to their Node 24 releases (`checkout`/
  `setup-python` v7, `upload-artifact` v7, `download-artifact` v8,
  `upload-pages-artifact`/`deploy-pages` v5).

### Documentation

- **The documentation is split into a general part and optional modules.**
  The general part covers the core API, the backends and the tutorials that
  work on every backend. "Optional modules" has one page per module
  (`drm_entities`, the new `rico_entities` page, `rdf_schema`, `schema_gen`,
  `torch_dataloader`, `text2cypher`), each with its installation notes,
  examples, own tutorials and API (moved from `docs/api/` to
  `docs/optional/`). Backend-specific examples are grouped by backend
  (NetworkX: vector search; Neo4j: propagation demo; Memgraph: none needed).
  The README follows the same structure.

- README, Sphinx docs and `test/README.md` now state explicitly that
  cvcdocdb targets Neo4j Community Edition, and that the Enterprise features
  need a license and are not fully tested.

## [1.4.0] - 2026-09-28

### Added

- **Primary-key indexes on Neo4j** (backward-compatible: nothing changes
  unless you ask for it). Every `insertNode`/pk lookup runs
  `MATCH (n:Label) WHERE <pk>`, which without an index scans every node of
  the label — a full migration grew quadratically.
  - `GraphStore.ensure_pk_indexes(pk_shapes=None)`: creates a non-unique
    index per `(main_label, pk properties)` shape (named
    `cvcdocdb_pk_<Label>_<props>`), reusing any equivalent existing index.
    Several pk shapes per label are fine (one index each), so this keeps
    the current "same label, different pk shapes" design. No-op on
    `NetworkXGraph`, which has its own in-memory pk index.
  - `Neo4jGraph(..., auto_pk_indexes=False)`: when True, indexes new pk
    shapes right after each commit (a standalone `insertNode` or a whole
    `batch()`); a failure only warns, the data is already committed.
  - `migrate(..., create_indexes=False)`: when True, indexes on *target*
    every pk shape migrated, after the migration commits
    (`MigrationStats.pk_indexes_created`).
  - Requires `INDEX MANAGEMENT` on the database (`PermissionError`
    otherwise), and never runs inside `batch()` (`RuntimeError`): Neo4j
    forbids schema changes in a transaction that also writes data.

### Fixed

- **`NetworkXGraph.schema_yaml()` detected a WeakNode's parent
  non-deterministically**, so the generated entity classes could change
  from one run to the next. When two labels tied as parent candidates
  (same pk overlap), the first one found while iterating a `set` of label
  names won — an order that changes with each Python process
  (`PYTHONHASHSEED`); the release test
  `test_regiofisica_generated_as_weaknode_with_fons_parent` failed about
  half of the time, also on 1.3.0. Ties are now broken by the real evidence
  — the candidate with a propagating (`_propagate`) edge to the child label
  — and, as a last resort, alphabetically.
- **Neo4jGraph no longer floods the log with `id()` deprecation notices.**
  cvcdocdb uses `id()` throughout (its public node ids are integers;
  `elementId()` returns strings, a breaking change kept for the next major
  version) and Neo4j 5 logs a deprecation notice per query: a
  ~100-node migration logged 479 of them, hiding the real warnings. The
  graph's own session now filters the DEPRECATION notification category —
  only that one, other notifications (e.g. unknown properties) still reach
  the log — when the server supports notification filters (Bolt 5.2+ /
  Neo4j 5.7+; older servers get no filter) and the caller didn't pass its
  own `notifications_*` driver settings.
- `cvcdocdb.__version__` was stuck at `1.0.0`; it now reports the installed version.

## [1.3.0] - 2026-09-27

### Added

- **Bounded waits, so a stuck writer or an unresponsive Neo4j server can't
  hang everything else forever** (backward-compatible: defaults unchanged).
  - `NetworkXGraph(persistence_path, lock_timeout=None)`: maximum seconds
    a mutating call, `batch()` or `migrate()` waits for the cross-process
    file lock. On expiry it raises the new `cvcdocdb.GraphLockTimeout`
    (a `TimeoutError`) naming the locked file, instead of blocking in
    `FileLock.acquire()` indefinitely. `None` keeps waiting forever.
  - `Neo4jGraph(url, user, password, database=None, **driver_config)`:
    extra keyword arguments are forwarded to `neo4j.GraphDatabase.driver()`,
    e.g. `connection_timeout`/`connection_acquisition_timeout` to bound
    connecting to an unresponsive server (the driver's
    `connection_timeout` alone does not bound the Bolt handshake —
    `connection_acquisition_timeout` does). They do not bound a query
    already running on a server that stops responding; a caller that needs
    that must run the work in a process it can terminate.
  - Found in production: `migrate()` holds the source `NetworkXGraph`'s
    lock for the whole migration, so a Neo4j target that stopped answering
    left every other writer of that `.pkl` blocked with no way out.
- **`cvcdocdb.text2cypher.Text2Cypher(graph, llm)`** — natural-language
  questions over a graph, with the same API for every backend (a script
  doesn't change when the backend does). `query()` returns the Cypher
  generated by the LLM plus the rows, shaped like `graph.query(cypher)`.
  - The LLM is a handle carrying its own configuration: a plain
    `prompt -> str` callable (wrapped in the new `CallableLLM`) or any
    object with `invoke()` (e.g. `neo4j_graphrag` LLM classes).
  - `Neo4jGraph`: built on `neo4j_graphrag`'s `Text2CypherRetriever`,
    reusing the graph's own driver and database (no Neo4j credentials passed
    again). New optional extra: `pip install cvcdocdb[graphrag]`
    (`neo4j-graphrag>=1.21,<2`, Python >= 3.10).
  - `NetworkXGraph` (or any backend with a Cypher-capable `query()`):
    handled by cvcdocdb itself, no extra dependency.
  - Only read-only Cypher is executed; anything else raises the new
    `Text2CypherError`.
  - The prompt's schema is introspected from the graph (on Neo4j with
    built-in `db.schema.*` procedures, so APOC is not required).

### Fixed

- **The published wheel declared no dependencies.** `setup.py` read
  `requirements.txt`, which isn't shipped in the sdist, so the wheel built
  from it had an empty `install_requires` and users had to install `neo4j`,
  `filelock`... by hand. Runtime dependencies are now declared in
  `setup.py` (`neo4j`, `networkx`, `numpy`, `filelock`, `tqdm`), with
  optional features as extras: `rdf`, `schema`, `vector`, `torch`,
  `graphrag`. `requirements.txt` (which also lists development-only tools
  such as `sphinx` and `notebook`) is now only for the development
  environment: install from source with `pip install -e . -r
  requirements.txt`. A test builds the package as it is published and
  checks the wheel's metadata.
  - When an optional dependency is missing, the error now names the extra
    to install (e.g. `pip install "cvcdocdb[rdf]"`, `cvcdocdb[schema]`,
    `cvcdocdb[vector]`, `cvcdocdb[torch]`) instead of the bare package,
    so an application knows what to add to its own requirements. The
    `torch_dataloader` messages, previously in Catalan, are now in English
    like the rest.

- **`NetworkXGraph.query(cypher)` gave wrong results for many basic read
  queries** — `WHERE` was ignored except for `=`, labelled relationship
  patterns (`(a:A)-[:R]->(b:B)`) matched nothing, `ORDER BY`/`SKIP`,
  grouping, `OPTIONAL MATCH` and alternative labels didn't work. Read-only
  queries now go through a new parser-based engine (`cvcdocdb.nx_cypher`)
  with Neo4j semantics for the supported subset: `MATCH`/`OPTIONAL MATCH`
  (node/relationship chains, `<-`/`->`/undirected, `:T1|T2`, inline
  property maps, several comma-separated patterns, relationship
  uniqueness), `WHERE` (comparisons, `AND`/`OR`/`XOR`/`NOT` with `null`
  logic, `IS [NOT] NULL`, `IN`, `STARTS WITH`/`ENDS WITH`/`CONTAINS`,
  `=~`, label predicates, arithmetic), `WITH`, `RETURN [DISTINCT]`,
  `ORDER BY`, `SKIP`, `LIMIT`, `count`/`sum`/`avg`/`min`/`max`/`collect`
  (with `DISTINCT` and implicit grouping) and common scalar functions.
  `$params` are bound as values instead of being pasted into the query
  text. A differential test checks identical results against a real Neo4j.
  - Behaviour changes: unsupported read syntax (variable-length paths,
    path variables, `UNWIND`, `UNION`, `CASE`, subqueries, unknown
    functions) now raises `ValueError` instead of silently returning
    wrong results, and returned nodes' `labels` now include alternative
    labels (as on Neo4j). Write queries are unchanged.

## [1.2.0] - 2026-09-23

### Added

- **`cvcdocdb.migration.migrate(source, target, ...)`** — generic
  backend-to-backend graph migration (e.g. `NetworkXGraph` ↔
  `Neo4jGraph`), in three phases: nodes, then edges, then vector
  indexes. Works between any two `GraphStore` implementations using
  only the common interface — it doesn't know about `WeakNode`,
  `Individu`/`be_value_properties`, or any other Python-level entity
  class.
  - Matches nodes by `(main_label, pk)`; falls back to using every
    property as the pk when the source can't tell which properties
    form it (Neo4j never persists that distinction — see below).
  - Reads/writes in configurable chunks, with a batched round-trip path
    for a `Neo4jGraph` source/target instead of one query per
    node/edge.
  - Optional `label_filter`/`property_filter` (same MongoDB-style
    syntax as `GraphDataset`); an edge is migrated only if both
    endpoints were kept.
  - `update`/`replace` conflict handling on the target (idempotent
    re-runs by default); `on_error="raise"|"skip"` for a best-effort
    migration of a large, possibly messy graph.
  - Verified end-to-end against a real Neo4j with the Karate Club
    dataset in both directions, including a full node/edge
    property-by-property diff (no property added or lost).
- **`Neo4jGraph.get_node_attrs(node_id)`** — was missing entirely
  (silently inherited a no-op default), so a generic caller (like
  `migrate()`) could not read a Neo4j node's attributes at all. Always
  reports `pk=None`, since Neo4j doesn't persist which properties form
  a node's primary key.
- **`Neo4jGraph.batch()`** — a context manager that groups multiple
  `insertNode`/`insertRelation` calls into a single transaction
  (committing once instead of once per call), for bulk writes.
- **`GraphStore.list_vector_indexes()`** (and `NetworkXGraph`/
  `Neo4jGraph` implementations) — introspects enabled vector (ANN)
  indexes, so `migrate()` can recreate them on the target.
- **Release-only cross-backend test suite** (`test_release_schema_migration.py`,
  new `release` pytest marker) — migrates the Karate Club dataset and a
  small `drm_entities` graph between `NetworkXGraph`/`Neo4jGraph` and
  compares the `schema_gen`-generated Python classes on both sides.
  Skipped by default (even under `-m slow`); run explicitly with
  `CVCDOCDB_RUN_RELEASE_TESTS=1 pytest -m release` before a release —
  see `test/README.md`.
- **CI**: `.github/workflows/test.yml` now runs `unit`+`integration`+`slow`
  on every push/PR (with a real `neo4j:5-community` service container),
  and `python-publish.yml` gates the PyPI publish on a `release-tests`
  job (including the `release`-marked tests above) — previously that
  workflow ran no tests at all before building and publishing.
- **`migrate()` now holds one write lock on *both* `source` and `target`
  for the entire migration** (all three phases, not one lock per
  phase — vector indexes were previously not locked at all). It's a
  write lock: concurrent reads of either store are never blocked, only
  concurrent writes are, so migration can't be torn by another writer
  mid-flight.
  - **`NetworkXGraph.batch(write=True)`** — new public method, a thin
    wrapper around the existing cross-process file lock
    (`_guarded_write()`), giving `NetworkXGraph` the same
    lock-for-a-whole-block contract `Neo4jGraph.batch()` already had.
    `write=False` still acquires the lock (blocking concurrent writers)
    but skips the resave on exit, for read-only use — `migrate()` uses
    this for `source`, so it doesn't rewrite the whole source graph to
    disk just for having been read.
  - `Neo4jGraph.batch()` now also accepts `write` (ignored — a
    read-only Neo4j transaction commit is already a no-op), for
    interface symmetry.
  - `Neo4jGraph.query()`/`get_node_ids()` now reuse `self._tx` when one
    is already open (same pattern `insertNode`/`insertRelation` already
    followed), so reading from a `Neo4jGraph` `source` inside its own
    `batch()` transaction works instead of erroring on a second,
    conflicting implicit transaction on the same session.
  - Neo4j has no literal whole-database lock; the closest honest
    approximation is a single transaction spanning the whole function —
    documented as such in `migrate()`'s docstring, including the
    trade-off that a very large migration now accumulates its whole
    write set in one uncommitted transaction instead of committing
    per-phase.

### Fixed

- **`NetworkXGraph.get_node_pks()` always returned `main_label=''`** —
  it read the label from the raw networkx node's own attribute dict,
  but `main_label`/`labels` are only ever stored in the separate
  `self._node_attrs` tracking dict. Undetected because every existing
  caller only checked the returned `pk`, never `main_label`. Fixed to
  read from `self._node_attrs`, like `get_node_attrs()` already does.
- **`Neo4jGraph.enable_vector_index()`/`query_vector_index()`** used
  the parameter name `name` instead of `property_name` like
  `GraphStore` and `NetworkXGraph` — a caller passing the documented
  keyword name got a `TypeError` instead of the intended
  `NotImplementedError`. Renamed for consistency.
- **`Neo4jGraph.query()` unconditionally rejected MongoDB-style dict
  filters**, even though `NetworkXGraph.query()` has always supported
  them as a documented "hybrid API" (dict or Cypher string) — breaking
  the package's own portability guarantee that client code works
  unchanged against either backend. Not a regression: this never
  worked, since the module's first commit. Now translates the same
  operator set `NetworkXGraph`'s matcher supports (`$eq $ne $gt $gte
  $lt $lte $in $nin $exists $regex $contains` and `$or`/`$and`/`$not`)
  into a parameterized Cypher `WHERE` clause, with `main_label`
  special-cased to filter on the real Neo4j label instead of a
  property.
- **`NetworkXGraph.schema_yaml()` dropped edge properties** (e.g. a
  `weight` on a relation) from the generated schema/code — it read
  `self._graph`'s own edge data, which `insertRelation` only ever
  populates with `rel_type`; the real properties live in
  `self._edge_attrs` (same root cause as the `get_node_pks()` fix
  above, but for edges).
- **`Neo4jGraph.schema_yaml()` always reported a relationship's
  `src`/`dst` as `"Node"`** — `sample["a"].get("labels", ("Node",))[0]`
  looked up a *property* literally named `labels` (which real data
  never has) instead of the driver Node's real `.labels` attribute.
- **`Neo4jGraph.schema_yaml()`'s inferred `primary_key` was an
  arbitrary guess** (`pk_fields[:2]` — literally "the first two
  properties found"). Neo4j never persists which properties form the
  real pk (the same limitation `migrate()`'s pk fallback documents);
  every property is now used instead, which is at least honest about
  that limitation rather than silently wrong.

## [1.1.0] - 2026-09-22

### Added

- **`Node.get_attrs(store)`** — retrieves a node's attributes from a
  `GraphStore` and, when the node's Python class declares a non-empty
  `be_value_properties` (e.g. `IndividuPadro.be_value_properties = ("nom",
  "cognom1", "cognom2")`), automatically resolves each property from its
  connected `Atribut`/`Valor` node and merges it into the returned dict.
  A plain `Node`/`WeakNode` (no `be_value_properties`) is returned
  unchanged, with no extra lookups. Previously, retrieving a node's
  attributes (`get_node_attrs`, a Cypher query, ...) never included these
  value properties — you had to manually traverse the `NOM`/`COGNOM1`/...
  edges to the `Valor` nodes yourself.
- **`GraphStore.get_dependency_value(node_id, relation_type)`** — new
  single-hop lookup (implemented in both `NetworkXGraph` and `Neo4jGraph`)
  that follows one outgoing typed edge and returns the connected node's
  `name` property. Powers `Node.get_attrs`'s value-property resolution
  without ever scanning the whole graph.
- **`be_value_properties` now works on `WeakNode` subclasses, not just
  `Individu`** — the auto-materialisation of `be_value_properties` into
  `Atribut` dependencies (previously hardcoded in `Individu.__init__`) was
  generalised into `Node.__init__` itself, so any `Node` or `WeakNode`
  subclass that declares `be_value_properties` gets the same behaviour for
  free at insertion time, and `Individu.__init__` was simplified to rely
  on it instead of duplicating the logic.
- **`cvcdocdb.torch_dataloader`** — a new optional module providing a
  PyTorch/PyTorch Geometric dataloader over `GraphStore` backends:
  - `GraphDataset`/`GraphDataLoader`: streaming node iteration
    (zero-copy over `NetworkXGraph`, `SKIP`/`LIMIT` pagination over
    `Neo4jGraph`), with MongoDB-style label/property filtering.
  - `SubgraphDataset`/`PyGDataLoader`/`to_pyg_data`: k-hop ego subgraphs
    and full-graph conversion to `torch_geometric.data.Data`, with
    optional embeddings merged into `data.x` and kept separately on
    `data.emb`.
  - `to_hetero_edge_index_dict`: lightweight full-graph heterogeneous
    topology loader (single pass over edges, no node attributes) for
    algorithms that need the whole graph, e.g. `MetaPath2Vec`.
  - `split_edges_by_node_property`: N-way (train/val/test/...) edge
    split by a per-node property, with overlap detection.
  - `edge_embeddings`: combine node embeddings into an edge
    representation (hadamard/concat/average/l1/l2/dot) for link
    prediction.
  - A tutorial notebook (`torch_dataloader_bibliography.ipynb`) covers
    the full flow on the bibliography dataset: streaming, subgraphs
    with embeddings, `MetaPath2Vec` training, and link prediction.

### Fixed

- **`Neo4jGraph.query()` never converted a real Neo4j `Node` into the
  documented `{"labels": [...], "properties": {...}}` dict** — the
  detection check (`hasattr(value, "properties")`) never matched a real
  `neo4j.graph.Node` from the driver (it exposes `labels`/`items`/`keys`/
  `values`/`get`, not `.properties`), so any `RETURN n`-style Cypher query
  silently returned the raw driver object instead. This had gone
  undetected because the only test asserting that dict shape ran against
  `NetworkXGraph`'s own Cypher emulation, not a real Neo4j server. Fixed
  by checking `hasattr(value, "labels") and hasattr(value, "items")`
  instead.
- **`torch_dataloader.GraphDataset`'s Neo4j path silently dropped
  `$or`/`$and`/`$not`/`$regex`/`$contains` in `property_filter`** instead
  of applying them as an in-memory fallback (as already documented and
  already done for `NetworkXGraph`) — fixed by running the full filter
  through `_nx_match` on every fetched row.
- **`torch_dataloader.GraphDataset` always returned `node_id=None` for
  the Neo4j backend** — fixed by fetching `id(n)` alongside the node.
- **`torch_dataloader.GraphDataset`'s Neo4j `SKIP`/`LIMIT` pagination
  had no `ORDER BY`**, so row order (and therefore the per-worker
  slicing for multi-worker `DataLoader`) wasn't stable across
  round-trips — a node could be yielded twice or skipped entirely.
- **`torch_dataloader.to_pyg_data` crashed on an empty store** when
  `node_attrs` was left at its default of `None`.

## [1.0.0] - 2026-09-21

### Added

- **Local Neo4j via Docker for the test suite** — `test/conftest.py` now
  starts a disposable `neo4j:5-community` container automatically (via the
  new `testcontainers` test dependency, see `requirements-test.txt`) when
  no server answers on `NEO4J_DEV_URL`/`NEO4J_URL` and Docker is available,
  falling back to the existing auto-skip otherwise. A `docker-compose.neo4j.yml`
  is also provided for running one manually. See `test/README.md`.

### Fixed

- **`Neo4jGraph._insertNode` treated a valid Neo4j internal node id of `0`
  as a missing parent** — the WeakNode parent check used
  `if not self.checkNode(node["parent"])`, and Neo4j's internal ids are
  0-indexed, so the very first node created after a full DB wipe (id `0`)
  was incorrectly reported as "missing parent node" even though it had
  just been inserted correctly. Changed to an explicit `is None` check.
- **`Neo4jGraph.insertNode(update=False, replace=False)` silently created
  a duplicate node** instead of refusing the insert when one with the same
  primary key already existed — unlike `NetworkXGraph`, which already
  raised `RuntimeError("Duplicate key: ...")` in this case. Two earlier
  attempts to fix this via a Neo4j `NODE KEY` constraint were reverted
  (the same `main_label` is reused across callers with different pk
  shapes, so a DB-level constraint for one shape broke inserts using
  another); duplicate-key detection is now done in Python instead,
  mirroring `NetworkXGraph`'s behaviour.
- **Renamed the obsolete "ADGT"/"XPP" naming** in `Neo4jGraph`'s
  exception/log messages (and one test class name) to "CVCDocDB".

## [1.0.0a4] - 2026-09-19

### Added

- **Cross-process write locking for `NetworkXGraph`** — every mutating method
  (`insertNode`/`insertRelation`/`deleteNode`/`create_group`/
  `init_propagation`/`enable_vector_index`) now runs its load-mutate-save
  cycle inside `_guarded_write()`, a reentrant context manager built on a
  `filelock.FileLock` (new dependency) next to the persistence file. On the
  *outermost* call it acquires the cross-process lock, reloads the latest
  on-disk state, lets the mutation run, and saves before releasing.
  Without this, two `NetworkXGraph` instances (in one process or several)
  pointing at the same `persistence_path` each hold an independent
  in-memory copy: whichever saves last silently drops any change the other
  already persisted — a lost update the previous `close()` fix (below)
  did not address, since it only stopped a purely-reading instance from
  clobbering a writer on close. Reloading immediately before every
  mutation, under a held lock, closes the "two writers" case too. Nested
  calls from the same top-level mutation (`create_group()` calling
  `insertNode()`/`insertRelation()`, `deleteNode()`'s cascade recursion)
  detect the lock is already held and skip the reload/save, so a group of
  changes still reaches disk as a single atomic write, and a failed
  multi-step operation leaves the on-disk state untouched rather than
  partially written. See `test/test_networkx_concurrent_writes.py`.

### Fixed

- **`NetworkXGraph.close()` unconditionally re-saved the whole persistence
  file** — even for an instance that only ever did reads. Every mutating
  method (`insertNode`/`insertRelation`/`deleteNode`/`enable_vector_index`)
  already persists synchronously right after it mutates, so `close()`'s
  own save was always redundant for a mutator and actively harmful for a
  read-only instance: two overlapping opens of the same path (e.g. a slow
  read in one process/request and a write in another) would race, and
  whichever instance closed *last* — even if it never wrote anything —
  silently overwrote the other's changes with its own (possibly stale)
  load-time snapshot. Found integrating this into a downstream app: 48
  real nodes were reduced to 1 after nothing but read-only queries ran
  concurrently with a write. `close()` no longer saves at all;
  `init_propagation()` (the one mutator that didn't already persist
  inline) now does so explicitly. Added
  `test/test_close_no_unconditional_save.py`, which reproduces the exact
  data-loss scenario and fails without the fix.

- **`Neo4jGraph._insertNode` treated a valid Neo4j internal node id of `0`
  as a missing parent** — the WeakNode parent check used
  `if not self.checkNode(node["parent"])`, and Neo4j's internal ids are
  0-indexed, so the very first node created after a full DB wipe (id `0`)
  was incorrectly reported as "missing parent node" even though it had
  just been inserted correctly. Changed to an explicit `is None` check.
- **`Neo4jGraph.insertNode(update=False, replace=False)` silently created
  a duplicate node** instead of refusing the insert when one with the same
  primary key already existed — unlike `NetworkXGraph`, which already
  raised `RuntimeError("Duplicate key: ...")` in this case. Two earlier
  attempts to fix this via a Neo4j `NODE KEY` constraint were reverted
  (the same `main_label` is reused across callers with different pk
  shapes, so a DB-level constraint for one shape broke inserts using
  another); duplicate-key detection is now done in Python instead,
  mirroring `NetworkXGraph`'s behaviour.
- **Renamed the obsolete "ADGT"/"XPP" naming** in `Neo4jGraph`'s
  exception/log messages (and one test class name) to "CVCDocDB".

## [1.0.0a3] - 2026-09-19

### Security

- **Complete label validation in `neo4j_graph.py`'s `insertNode`** — the
  1.0.0a2 fix only validated `main_label`; `alternative_labels` and
  dependency nodes (inserted via the internal `_insertNode` path) were
  not checked and could still inject Cypher through the node's label
  list. Validation now runs inside `_insertNode` itself, covering every
  path that reaches it.
- **Parameterized Cypher in the RiC-O/NAF example loader** — `cvcdocdb.exemples.load_ric_o_naf`
  built Cypher via raw string interpolation of node/relationship ids and
  property values extracted from RDF/XML downloaded from GitHub,
  bypassing the validators added in 1.0.0a2. Now uses `Neo4jGraph.query()`'s
  `params` argument instead of splicing untrusted values into query text.

## [1.0.0a2] - 2026-09-18

### Security

- **Code-injection prevention in `schema_gen.py`** — untrusted, ontology-derived
  strings (class names, property names, `rdfs:comment` docstrings) are now
  validated as safe Python identifiers or escaped with `repr()` before being
  spliced into generated entity-class source, closing an arbitrary-code-execution
  path via a crafted RDF/OWL ontology.
- **Cypher-injection prevention in `neo4j_graph.py`** — `pk` values, node
  labels, and relation types are now escaped/validated before being
  interpolated into `WHERE`/`MERGE`/`MATCH` clauses.
- **Hardened default pickle persistence path in `networkx_graph.py`** —
  `NetworkXGraph()`'s default persistence file now lives in a per-user,
  permission-restricted cache directory instead of the shared system temp
  dir, and refuses to unpickle a file it doesn't own.

### Fixed

- **`Node.__getitem__`/`__setitem__`** — custom attributes set via kwargs
  are now readable through the dict-like interface, and `node["pk"] = ...`
  no longer raises `KeyError`.
- **`_mergePK`** — no longer mutates the caller's own `pk` dict in place.
- **`schema_gen.py` generated classes corrupted partial `insertNode`/`insertRelation`
  merges** — every generated `Node`/`Relation`/`WeakNode` subclass unconditionally
  assigned `self.<prop> = kwargs.get("<prop>", "<type-name>")` for every schema
  property in `__init__`, including primary-key fields. Since `Node.__init__`/
  `Relation.__init__` already set an attribute for whatever kwargs the caller
  actually passes, this line only mattered when the caller *omitted* a field —
  in which case it invented that attribute with the literal type-name string
  (`"string"`, `"integer"`, ...) as its value. Because `update=True` merges
  send every attribute present on the object, this silently overwrote the
  field's real stored value on any partial update (e.g. `User(pk={"email": e},
  is_owner=False)` would blank out `nom`/`password`/`role` to the literal
  string `"string"`). Properties are now declared as bare class-level type
  annotations (`prop: Optional[str]`) instead — these inform IDEs/type
  checkers but never touch the instance `__dict__`, so a generated class can
  no longer invent a value for a field the caller didn't pass. Added a
  regression test (`test_generated_class_partial_update_preserves_fields`)
  that reproduces the corruption end-to-end through `NetworkXGraph`.

### Changed

- **Package renamed `drm` → `cvcdocdb`** — the source package directory, all
  imports, `setup.py` metadata (`drm-tools` → `cvcdocdb`), docs, and example
  scripts now use the `cvcdocdb` name throughout, matching the project's
  actual distribution name. Older changelog entries below still reference
  `drm/...` paths as they existed at the time; they are left as a historical
  record rather than rewritten.
- **Test suite reorganized** — unit tests misclassified inside
  `test_drm.py` moved to `test_node.py`/`test_relation.py`; `slow` (Neo4j)
  tests now auto-skip with a clear reason when no server is reachable
  instead of failing on connection timeouts.
- **README restructured** — general "Document Representation Models"
  introduction added before the CVCDocDB-specific section.

## [1.1.0] - 2026-07-13

### Added

- **NetworkX MERGE support** — Full MERGE clause in Cypher executor: node patterns `(n:Label {props})` and edge patterns `(a)-[r:REL]->(b)` with binding resolution from preceding MATCH clauses.
- **NetworkX SET clause fix** — Now handles backtick-quoted properties (`n.\`prop\``) and space-separated multiple assignments.
- **NetworkX MATCH binding fusion** — Multiple MATCH clauses correctly merge bindings via cross-product instead of replacing.
- **RiC-O loader rewrite** — `load_ric_o_naf.py` uses Cypher MERGE instead of CSV import, with proper GitHub API directory listing and exponential backoff retries.
- **Security** — Real Neo4j passwords removed from notebooks, `.env.example`, and test files.

### Fixed

- **Sphinx warnings** — Added `rdf_schema.rst` and `schema_gen.rst` to toctree; added `:no-index:` for duplicate `DocumentCultural.document_class`.
- **NetworkX edge MERGE regex** — Fixed pattern to correctly match `(a)-[r:REL]->(b)` format.

## [1.1.0a3] - 2026-07-11

### Changed

- **Author metadata** — Single author in `setup.py` (`Oriol Ramos Terrades`); full contributor list remains in README.rst "Authors and Contributors" section

## [1.1.0a2] - 2026-07-11

### Changed

- **PyPI metadata** — Cleaned up `setup.py`: proper author string, single author email, SPDX license identifier, `project_urls` for docs/source/tracker, `package_dir` fix, improved keywords, updated classifier to `Development Status :: 4 - Beta`, `OS Independent`

## [1.1.0a1] - 2026-07-11

### Added

- **`GraphStore` ABC** (`drm/graph_store.py`) — Abstract base class defining the interface for all graph backends. Declares 6 abstract methods (`insertNode`, `insertRelation`, `deleteNode`, `checkNode`, `create`, `close`) and provides concrete default implementations for helper methods.
- **Contract tests** (`test/test_graph_store_contract.py`) — 47 test methods verifying propagation policies (ON DELETE CASCADE, RESTRICT, SET NULL, ON UPDATE CASCADE), WeakNode composite PKs, cascade delete, FK violations, replace behaviour, bulk import, checkNode, duplicate key, and close safety.
- **`pk=None` explicit parameter** — `Node(pk=None, main_label="X")` creates a node with `_primary_key = None`. The backend assigns an auto-generated ID as the primary key after insertion.
- **`.env` file** — Template with Neo4j connection settings. Listed in `.gitignore`.
- **`NetworkXGraph` transparent persistence** — The graph store persists state to disk and reloads automatically on initialization.
- **`NetworkXGraph` secondary indexes** — Added PK index and property index for exact-match searches, with new query helpers (`find_nodes_by_property`, `find_nodes`).
- **`NetworkXGraph` optional vector indexes** — Added user-triggered ANN indexing for vector properties via `hnswlib`, including `enable_vector_index()` and `query_vector_index()`.
- **`GraphStore` vector API** — Added package-wide vector-index interface methods so vector capabilities are exposed consistently across backends.
- **RDF/OWL ontology pipeline** — `drm/rdf_schema.py` provides full pipeline: download RDF → convert to DRM YAML → generate Python entity classes. Supports Turtle, RDF/XML, N-Triples, and other RDF formats.
- **Unified class generator** — `drm/schema_gen.py` generates Python entity classes from YAML schemas with `primary_key` detection (optional vs mandatory) and `doc` field for docstrings.
- **RiC-O entities** — `drm/rico_entities.py` auto-generated from RiC-O ontology: 677 classes (1 Node, 106 WeakNode, 464 Relation, 106 WeakRelation).
- **Test organization** — Three test levels with pytest markers: unit (43), integration (215), slow/Neo4j (44) = 302 total.
- **Tutorial notebooks** — Complete set of Jupyter notebooks organized into `intro/`, `interactive/`, and `datasets/` subdirectories.
- **API documentation** — Sphinx docs for `rdf_schema` and `schema_gen` modules.
- **Transactional group creation** (`create_group()`) — Atomically creates a strong node with its WeakNodes and WeakRelations in an isolated transaction. NetworkX uses snapshot/restore rollback; Neo4j uses `session.write_transaction()`.
- **Lazy background propagation initialization** (`init_propagation()`) — Scans the graph to initialize `is_weak`, `_propagate`, `parent_relation`, and `_dependencies` properties on existing nodes. Supports sync, background thread, and progress callback modes. Idempotent (returns False on second call).
- **`_weak_init_done` tracking** — Hidden property on strong nodes to mark whether their WeakNodes have been initialized via `create_group()`. Prevents `init_propagation()` from reprocessing already-initialized groups.
- **`propagate` from YAML** — `schema_gen.py` reads `propagate` flag from weak_relations YAML entries and passes it to WeakRelation constructors in generated code.
- **Neo4j propagation demo** — `drm/exemples/demo_propagation.py` connects to Neo4j DEV, loads GOT characters, generates YAML + Python classes, initializes propagation, and runs sample queries.
- **Interactive propagation demo notebook** — `docs/tutorials/notebooks/interactive/propagation_demo.ipynb` mirrors the Python demo as a Jupyter notebook.

### Changed

- **`MockGraph` → `NetworkXGraph`** — Class and module renamed throughout the codebase.
- **`Neo4jGraph` inherits from `GraphStore`** — Now implements the `GraphStore` ABC interface.
- **`drm/entities.py` → `drm/drm_entities.py`** — Module renamed to follow project convention.
- **Entity hierarchy refactored** — Introduced base classes to reduce duplication:
  - `Individu` → `IndividuPadro`, `IndividuFoto`
  - `Lloc` → `LlocPadro`, `LlocFoto`
  - `DocumentCultural` → `Fons`, `ActaTemporal`
  - `Layout` → `RegioFisica`, `OCRTranscript`
  - `_DocumentCulturalBase` → `Padro`, `Fotografia`, `BOE`
- **Lazy imports** — `Neo4jGraph` and `NetworkXGraph` are now lazily imported in `drm.__init__` to avoid `ImportError` when optional dependencies are not installed.
- **`README.md` completely rewritten** — Restructured with tables, added RDF/OWL pipeline examples, consolidated notebook list.
- **`docs/index.rst` completely rewritten** — Added Features, Query & Filtering, Vector Indexing, Configuration, and Acknowledgements sections.
- **Method ordering** — Reordered `__init__`, public methods, protected helpers, and static helpers in all graph classes following PEP 8 conventions.
- **Tutorial notebook organisation** — Removed `legacy/` directory. Reorganized tutorials into `intro/`, `interactive/`, and `datasets/` subdirectories.
- **Output file naming** — RDF-to-Python pipeline now generates `{db}_entities.py` instead of `entities_{db}.py`.

### Fixed

- **`_UNSET` duplicate in `base.py`** — Removed duplicate `_UNSET` sentinel constant.
- **`_mergePK` with `pk=None`** — Fixed `AttributeError` when merging a child's `None` PK with parent's PK. WeakNodes with `pk=None` now inherit the parent's PK before insertion.
- **Neo4jGraph PK validation** — Skip parent-child PK validation for WeakNodes with backend-assigned IDs.
- **NetworkXGraph unique IDs** — WeakNodes with `pk=None` now receive unique IDs from the backend.
- **Removed session artifacts** — Deleted temporary markdown files from previous development sessions.
- **`IndividuFoto.be_value_properties`** — Added missing attribute that caused `AttributeError`.
- **`Esdeventiment` duplicate `pk` kwarg** — Fixed `TypeError` from passing `pk` both explicitly and via `**kwargs`.
- **`IndividuPadro.ignore_assertion` test** — Added missing `pk=1` argument.
- **`WeakNode` parent validation** — Nodes with `pk=None` (transient) cannot be parents; raises `ValueError`.
- **`__repr__` for nodes with `_primary_key = None`** — No longer crashes.
- **`version` setter** — Skips `_single_pk` transformation when `_primary_key` is `None`.
- **`__setitem__` with `key="attributes"`** — Uses `pop(..., None)` instead of `pop(...)` to handle missing `_primary_key`.

### Documentation

- **`docs/api/rdf_schema.rst`** — New API documentation for RDF/OWL conversion module.
- **`docs/api/schema_gen.rst`** — New API documentation for schema-based class generation.
- **Tutorial: generating classes from OWL** — `generating_classes_from_owl.ipynb` demonstrates the full pipeline with RiC-O, karate_club, and bibliografia datasets.
- **Test README** — `test/README.md` documents the three test levels and usage.
- **Notebook dependencies** — Added `notebook`, `ipykernel`, and `ipywidgets` to project environments.
