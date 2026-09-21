# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
