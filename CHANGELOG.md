# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Package renamed `drm` → `cvcdocdb`** — the source package directory, all
  imports, `setup.py` metadata (`drm-tools` → `cvcdocdb`), docs, and example
  scripts now use the `cvcdocdb` name throughout, matching the project's
  actual distribution name. Older changelog entries below still reference
  `drm/...` paths as they existed at the time; they are left as a historical
  record rather than rewritten.

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
