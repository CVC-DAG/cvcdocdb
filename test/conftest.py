"""Pytest configuration for cvcdocdb.

Organizes tests into three levels:

- **unit** — Fast, no graph store (Node, Relation, schema generation)
- **integration** — NetworkXGraph tests (in-memory, no Neo4j required)
- **slow** — Neo4j integration tests (require real Neo4j connection)

Usage::

    # Run all tests
    pytest test/ -v

    # Run only unit tests (fast, ~43 tests)
    pytest test/ -v -m unit

    # Run NetworkX integration tests (~216 tests)
    pytest test/ -v -m integration

    # Skip Neo4j tests (CI without Neo4j)
    pytest test/ -v -m "not slow"

    # Run only Neo4j tests
    pytest test/ -v -m slow
"""

import os
import socket
from urllib.parse import urlparse

import pytest


def _neo4j_reachable(timeout: float = 0.5) -> bool:
    """Quick TCP probe so unreachable-Neo4j runs skip fast instead of timing out.

    Without this, every `slow`-marked test independently tries (and fails)
    to connect, which is what made a plain `pytest test/` run ~4x slower
    than `pytest test/ -m "not slow"` locally.
    """
    url = os.environ.get("NEO4J_DEV_URL") or os.environ.get("NEO4J_URL") or "bolt://localhost:7687"
    parsed = urlparse(url)
    host, port = parsed.hostname or "localhost", parsed.port or 7687
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "unit: fast tests with no graph store"
    )
    config.addinivalue_line(
        "markers", "integration: NetworkXGraph tests (in-memory)"
    )
    config.addinivalue_line(
        "markers", "slow: Neo4j integration tests (require real Neo4j)"
    )


def pytest_collection_modifyitems(config, items):
    """Automatically add markers based on test file path.

    test_graph_store_contract.py has individual @pytest.mark.integration
    and @pytest.mark.slow decorators on each test method, so we skip it here.
    """
    unit_files = {
        "test_node.py",
        "test_relation.py",
        "test_schema_gen.py",
        "test_rdf_schema.py",
    }
    nx_files = {
        "test_drm.py",
        "test_nx_query_integration.py",
        "test_query_method.py",
        "test_schema_generation.py",
        "test_schema_weaknode.py",
        "test_schema_weak_relation.py",
    }

    neo4j_files = {
        "test_create_graph.py",
        "test_neo4j_real.py",
    }

    neo4j_ok = _neo4j_reachable()
    skip_unreachable = pytest.mark.skip(
        reason="Neo4j unreachable (no server at NEO4J_DEV_URL/NEO4J_URL) — "
        "skipping 'slow' test instead of timing out on connection"
    )

    for item in items:
        filename = item.fspath.basename
        if filename in unit_files:
            item.add_marker(pytest.mark.unit)
        elif filename in nx_files:
            item.add_marker(pytest.mark.integration)
        elif filename in neo4j_files:
            item.add_marker(pytest.mark.slow)
        # test_graph_store_contract.py has individual markers on each method

        if not neo4j_ok and "slow" in {m.name for m in item.iter_markers()}:
            item.add_marker(skip_unreachable)
