# Tests

Three levels of tests, organized with pytest markers:

## Levels

| Level | Marker | Count | Time | Description |
|-------|--------|-------|------|-------------|
| **Unit** | `pytest -m unit` | 80 | ~2s | Fast, no graph store (Node, Relation, schema generation) |
| **Integration** | `pytest -m integration` | ~180 | ~3s | NetworkXGraph tests (in-memory, no Neo4j required) |
| **Neo4j** | `pytest -m slow` | 44 | ~2s locally (skipped) | Neo4j integration tests (require real Neo4j connection) |

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
