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

If no Neo4j server answers on ``NEO4J_DEV_URL``/``NEO4J_URL`` and Docker plus
the ``testcontainers`` package are available, a disposable ``neo4j:5-community``
container is started automatically (see ``_maybe_start_docker_neo4j`` below) so
``slow`` tests can run locally without any manual setup. It is torn down at the
end of the test session. See ``test/README.md`` for details.
"""

import atexit
import os
import shutil
import socket
from urllib.parse import urlparse

import pytest

_DOCKER_NEO4J_IMAGE = "neo4j:5-community"
_DOCKER_NEO4J_USER = "neo4j"
_DOCKER_NEO4J_PASSWORD = "neo4j2026"
_DOCKER_NEO4J_BOLT_PORT = 7687
_DOCKER_NEO4J_HTTP_PORT = 7474

_docker_neo4j_container = None


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


def _maybe_start_docker_neo4j() -> None:
    """Start a disposable local Neo4j container if nothing is reachable.

    Best-effort: silently does nothing if a server already answers, Docker
    isn't installed, or the optional ``testcontainers`` dependency isn't
    installed (see requirements-test.txt). Any failure to start the
    container (e.g. ports already bound by something else) is reported but
    otherwise ignored — `slow` tests then fall back to the existing
    "Neo4j unreachable" auto-skip.
    """
    global _docker_neo4j_container

    if _neo4j_reachable():
        return

    if shutil.which("docker") is None:
        return

    try:
        from testcontainers.core.container import DockerContainer
        from testcontainers.core.waiting_utils import wait_for_logs
    except ImportError:
        return

    # Ryuk (testcontainers' cleanup sidecar) mounts the Docker socket into its
    # own container, which fails on some local Docker Desktop setups. We
    # already stop the container explicitly (atexit + session teardown), so
    # disable it rather than depending on that mount working.
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

    container = None
    try:
        container = (
            DockerContainer(_DOCKER_NEO4J_IMAGE)
            .with_env("NEO4J_AUTH", f"{_DOCKER_NEO4J_USER}/{_DOCKER_NEO4J_PASSWORD}")
            .with_bind_ports(_DOCKER_NEO4J_BOLT_PORT, _DOCKER_NEO4J_BOLT_PORT)
            .with_bind_ports(_DOCKER_NEO4J_HTTP_PORT, _DOCKER_NEO4J_HTTP_PORT)
        )
        container.start()
        wait_for_logs(container, "Bolt enabled", timeout=120)
    except Exception as exc:  # pragma: no cover - environment dependent
        # Covers: Docker CLI present but daemon not running, ports already
        # bound, image pull failure, etc. Falls back to the existing
        # "Neo4j unreachable" auto-skip.
        print(f"[conftest] Could not start local Neo4j docker container: {exc}")
        if container is not None:
            try:
                container.stop()
            except Exception:
                pass
        return

    os.environ.setdefault(
        "NEO4J_DEV_URL", f"bolt://localhost:{_DOCKER_NEO4J_BOLT_PORT}"
    )
    os.environ.setdefault("NEO4J_DEV_USER", _DOCKER_NEO4J_USER)
    os.environ.setdefault("NEO4J_DEV_PASSWORD", _DOCKER_NEO4J_PASSWORD)
    os.environ.setdefault("NEO4J_DEV_DATABASE", "neo4j")

    _docker_neo4j_container = container
    atexit.register(container.stop)
    print(
        f"[conftest] Started local Neo4j docker container "
        f"({_DOCKER_NEO4J_IMAGE}) on bolt://localhost:{_DOCKER_NEO4J_BOLT_PORT}"
    )


def pytest_configure(config):
    """Register custom markers and (if needed) start a local Neo4j container."""
    config.addinivalue_line(
        "markers", "unit: fast tests with no graph store"
    )
    config.addinivalue_line(
        "markers", "integration: NetworkXGraph tests (in-memory)"
    )
    config.addinivalue_line(
        "markers", "slow: Neo4j integration tests (require real Neo4j)"
    )
    _maybe_start_docker_neo4j()


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
