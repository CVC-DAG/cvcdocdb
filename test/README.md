# Tests

Four levels of tests, organized with pytest markers:

## Levels

| Level | Marker | Count | Time | Description |
|-------|--------|-------|------|-------------|
| **Unit** | `pytest -m unit` | 80 | ~2s | Fast, no graph store (Node, Relation, schema generation) |
| **Integration** | `pytest -m integration` | ~180 | ~3s | NetworkXGraph tests (in-memory, no Neo4j required) |
| **Neo4j** | `pytest -m slow` | 44 | ~2s locally (skipped) | Neo4j integration tests (require real Neo4j connection) |
| **Release** | `pytest -m release` | 3 | ~30s (requires Neo4j) | Cross-backend migration + schema codegen comparison — run on `main`, right before publishing a new version |

## Usage

```bash
# Run all tests (321 tests, ~5s without a local Neo4j)
pytest test/ -v

# Run only unit tests (fast, ~2s)
pytest test/ -m unit

# Run NetworkX integration tests (~3s)
pytest test/ -m integration

# Skip Neo4j tests explicitly (same effect as the automatic skip below)
pytest test/ -m "not slow"

# Run only Neo4j tests (requires a reachable server + NEO4J_DEV_* env vars)
pytest test/ -m slow
```

### Automatic Neo4j skip

`conftest.py` probes `NEO4J_DEV_URL` / `NEO4J_URL` (default `bolt://localhost:7687`)
with a 0.5s TCP connect before the run. If nothing answers, every `slow`-marked
test is auto-skipped with a clear reason instead of failing/erroring on a
connection timeout — this is what keeps a plain `pytest test/` fast (~5s)
even without a local Neo4j instance running. When a real server is reachable,
those tests run normally.

### Release tests

`test_release_schema_migration.py` covers `cvcdocdb.migration.migrate()` and
`cvcdocdb.schema_gen.generate_classes()` end to end: migrating a real dataset
between `NetworkXGraph` and `Neo4jGraph` and comparing the Python entity-class
code generated from each side's live schema (they must declare the same
classes and properties). These tests are **skipped by default** — even with
`pytest -m slow` — because they're the most expensive in the suite and only
meaningful right before a release. Run them explicitly:

```bash
# Requires a reachable Neo4j (NEO4J_DEV_* env vars)
CVCDOCDB_RUN_RELEASE_TESTS=1 pytest test/ -m release -v
```

Run this on `main`, after merging `develop` in and bumping `VERSION`, right
before `python -m build && twine upload`.

### Automatic local Neo4j via Docker

Install the extra test dependencies once:

```bash
pip install -r requirements-test.txt
```

If nothing answers on `NEO4J_DEV_URL`/`NEO4J_URL`, Docker is installed, and
the `testcontainers` package is available, `conftest.py` automatically starts
a disposable `neo4j:5-community` container (bolt on `7687`, HTTP on `7474`,
credentials `neo4j` / `neo4j2026` — matching `.env.example`) before the test
session runs, and stops it again when the session ends. This means
`pytest test/` (or `pytest test/ -m slow`) runs the real Neo4j tests locally
with zero manual setup, as long as Docker Desktop/Engine is running.

If Docker isn't available, or the container fails to start (e.g. ports 7687
or 7474 already in use), this is reported to stdout and the suite falls back
to the automatic skip described above.

To manage the container yourself instead (e.g. to keep it running across
multiple test runs), use the provided compose file:

```bash
docker compose -f docker-compose.neo4j.yml up -d
pytest test/ -m slow
docker compose -f docker-compose.neo4j.yml down
```

### Memgraph tests

`test_memgraph_graph.py` needs a Memgraph server at `MEMGRAPH_URL`
(`MEMGRAPH_USER`/`MEMGRAPH_PASSWORD`, `MEMGRAPH_DATABASE` optional). If nothing
answers there (default `bolt://localhost:7688`) and Docker plus
`testcontainers` are available, `conftest.py` starts a disposable
`memgraph/memgraph:3.13.1` container on host port 7688, next to the Neo4j one
on 7687. These tests are skipped only when Memgraph is unreachable, whether or
not Neo4j is available. `MemgraphMatchesNeo4jTest` runs every propagation
scenario on both backends and compares the resulting graphs and errors, so it
needs both.

### Apache Jena tests

`test_jena_graph.py` (and the Jena parts of `test_migration_propagation.py`
and `test_text2sparql.py`) need an Apache Jena Fuseki dataset with query and
update at `FUSEKI_URL` (e.g. `http://localhost:3030/ds`). If nothing answers
there and Docker plus `testcontainers` are available, `conftest.py` starts a
disposable `secoresearch/fuseki:6.2.0` container on port 3030, with a clean
TDB2 dataset `/ds`. Each test writes in its own namespace and deletes it
afterwards.

### Neo4j Enterprise tests (optional, not run in CI)

CI and the automatic Docker container use **Neo4j Community**, so the
Enterprise-only features (`Neo4jGraph(edition="enterprise")`) are only checked
without a server: the generated Cypher, and that Community mode refuses them.
To run `test/test_neo4j_enterprise.py::EnterpriseServerTest` against a real
**Neo4j Enterprise** server (this requires a license you are entitled to use):

```bash
export NEO4J_ENTERPRISE_URL=bolt://enterprise-host:7687
export NEO4J_ENTERPRISE_USER=neo4j
export NEO4J_ENTERPRISE_PASSWORD=...
export NEO4J_ENTERPRISE_DATABASE=neo4j   # optional
pytest test/test_neo4j_enterprise.py -m slow
```

## Test files

### Unit (no graph store)
- `test_node.py` — Node class internals (dict-like `__getitem__`/`__setitem__`, pk handling, WeakNode)
- `test_relation.py` — Relation class internals
- `test_schema_gen.py` — YAML-to-Python class generation
- `test_rdf_schema.py` — RDF/OWL-to-YAML conversion

### NetworkX integration
- `test_drm.py` — Full NetworkXGraph workflow, entities, and the mocked-driver Neo4jGraph tests
- `test_nx_query_integration.py` — Query and filtering
- `test_query_method.py` — Property-based search
- `test_schema_generation.py` — Schema YAML generation (Graph → YAML introspection; not to be confused with `test_schema_gen.py`, YAML → Python)
- `test_schema_weaknode.py` — WeakNode detection in schemas
- `test_schema_weak_relation.py` — WeakRelation inference
- `test_graph_store_contract.py::TestNetworkXGraph` — Contract tests

### Neo4j integration
- `test_create_graph.py` — Neo4j node/relation creation
- `test_neo4j_real.py` — Real Neo4j workflow tests
- `test_graph_store_contract.py::TestNeo4jGraph` — Contract tests
- `test_migration_propagation.py` — `migrate()` keeps every propagation property, between all backend pairs
- `test_optional_drm_entities.py` — `cvcdocdb.drm_entities` isn't imported by `import cvcdocdb`; deprecated top-level access still works
- `test_propagation_contract.py` — propagation properties of `init_propagation()`/`create_group()` on every backend, NetworkX atomic writes
- `test_weaknode_hierarchy.py` — WeakNode ancestry on NetworkX, depth limit warning, NetworkX vs Neo4j propagation parity
- `test_jena_rdf.py` — RDF layout of `JenaGraph` (no server)
- `test_jena_graph.py` — Jena backend: contract suites, propagation parity with NetworkX, atomic/concurrent writes, SPARQL (needs `FUSEKI_URL`)
- `test_text2sparql.py` — Text2SPARQL and Text2Query
- `test_memgraph_graph.py` — Memgraph backend: contract tests, propagation policy, comparison with Neo4j
- `test_neo4j_enterprise.py` — Community default / opt-in Enterprise mode (Enterprise server tests need `NEO4J_ENTERPRISE_URL`)

## CI

In CI environments without Neo4j, the automatic skip above already covers
this, but you can also be explicit:

```bash
pytest test/ -v -m "not slow"
```

## Not tests

`visualize_graph.py` and `visualize_mock_graph.py` are manual debugging
scripts (not picked up by pytest), not test suites — treat them as
developer tools that happen to live under `test/`.
