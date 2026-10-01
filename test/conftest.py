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

Likewise, if nothing answers on ``MEMGRAPH_URL`` (default
``bolt://localhost:7688``), a disposable Memgraph container is started on host
port 7688 (see ``_maybe_start_docker_memgraph``) for ``test_memgraph_graph.py``.
Those tests depend on Memgraph being reachable, not Neo4j. The Neo4j-vs-Memgraph
comparison (``MemgraphMatchesNeo4jTest``) needs both.
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

_DOCKER_MEMGRAPH_IMAGE = "memgraph/memgraph:3.13.1"
_DOCKER_MEMGRAPH_USER = "memgraph"
_DOCKER_MEMGRAPH_PASSWORD = "memgraph2026"
# Port de l'amfitrió diferent del de Neo4j perquè tots dos puguin conviure.
_DOCKER_MEMGRAPH_HOST_PORT = 7688
_MEMGRAPH_BOLT_PORT = 7687
_DEFAULT_MEMGRAPH_URL = f"bolt://localhost:{_DOCKER_MEMGRAPH_HOST_PORT}"

_docker_memgraph_container = None


def _bolt_reachable(url: str, timeout: float = 0.5) -> bool:
    """TCP probe of a Bolt URL."""
    parsed = urlparse(url)
    host, port = parsed.hostname or "localhost", parsed.port or 7687
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _memgraph_reachable(timeout: float = 0.5) -> bool:
    return _bolt_reachable(os.environ.get("MEMGRAPH_URL") or _DEFAULT_MEMGRAPH_URL, timeout)


def _neo4j_reachable(timeout: float = 0.5) -> bool:
    """Quick TCP probe so unreachable-Neo4j runs skip fast instead of timing out.

    Without this, every `slow`-marked test independently tries (and fails)
    to connect, which is what made a plain `pytest test/` run ~4x slower
    than `pytest test/ -m "not slow"` locally.
    """
    url = os.environ.get("NEO4J_DEV_URL") or os.environ.get("NEO4J_URL") or "bolt://localhost:7687"
    return _bolt_reachable(url, timeout)


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


def _maybe_start_docker_memgraph() -> None:
    """Start a disposable local Memgraph container if nothing is reachable.

    Same best-effort policy as :func:`_maybe_start_docker_neo4j`; on failure
    the Memgraph ``slow`` tests are skipped.
    """
    global _docker_memgraph_container

    if _memgraph_reachable():
        if not os.environ.get("MEMGRAPH_URL"):
            # Un Memgraph ja engegat al port per defecte (p. ex. el d'una
            # execució anterior): mateixes credencials que el contenidor.
            os.environ["MEMGRAPH_URL"] = _DEFAULT_MEMGRAPH_URL
            os.environ.setdefault("MEMGRAPH_USER", _DOCKER_MEMGRAPH_USER)
            os.environ.setdefault("MEMGRAPH_PASSWORD", _DOCKER_MEMGRAPH_PASSWORD)
        return
    if os.environ.get("MEMGRAPH_URL") or shutil.which("docker") is None:
        return
    try:
        from testcontainers.core.container import DockerContainer
        from testcontainers.core.waiting_utils import wait_for_logs
    except ImportError:
        return

    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    container = None
    try:
        container = (
            DockerContainer(_DOCKER_MEMGRAPH_IMAGE)
            .with_env("MEMGRAPH_USER", _DOCKER_MEMGRAPH_USER)
            .with_env("MEMGRAPH_PASSWORD", _DOCKER_MEMGRAPH_PASSWORD)
            .with_bind_ports(_MEMGRAPH_BOLT_PORT, _DOCKER_MEMGRAPH_HOST_PORT)
        )
        container.start()
        wait_for_logs(container, "You are running Memgraph", timeout=120)
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"[conftest] Could not start local Memgraph docker container: {exc}")
        if container is not None:
            try:
                container.stop()
            except Exception:
                pass
        return

    os.environ.setdefault("MEMGRAPH_URL", _DEFAULT_MEMGRAPH_URL)
    os.environ.setdefault("MEMGRAPH_USER", _DOCKER_MEMGRAPH_USER)
    os.environ.setdefault("MEMGRAPH_PASSWORD", _DOCKER_MEMGRAPH_PASSWORD)
    _docker_memgraph_container = container
    atexit.register(container.stop)
    print(
        f"[conftest] Started local Memgraph docker container "
        f"({_DOCKER_MEMGRAPH_IMAGE}) on {_DEFAULT_MEMGRAPH_URL}"
    )


def _preimport_neo4j_graphrag() -> None:
    """Import neo4j_graphrag before any test module is collected.

    test_drm.py replaces ``neo4j`` with a mock in ``sys.modules`` when it is
    imported, for the rest of the session. Text2Cypher imports
    neo4j_graphrag lazily, so importing it later would bind it to that mock
    and fail. Imported here, it binds to the real driver.
    """
    try:
        import neo4j_graphrag.exceptions  # noqa: F401
        import neo4j_graphrag.retrievers  # noqa: F401
    except ImportError:
        pass


def pytest_configure(config):
    """Register custom markers and (if needed) start a local Neo4j container."""
    _preimport_neo4j_graphrag()
    config.addinivalue_line(
        "markers", "unit: fast tests with no graph store"
    )
    config.addinivalue_line(
        "markers", "integration: NetworkXGraph tests (in-memory)"
    )
    config.addinivalue_line(
        "markers", "slow: Neo4j integration tests (require real Neo4j)"
    )
    config.addinivalue_line(
        "markers",
        "release: expensive cross-backend comparison tests (migration + "
        "schema codegen) — run only on main, right before publishing a "
        "new version. See test/README.md.",
    )
    _maybe_start_docker_neo4j()
    _maybe_start_docker_memgraph()


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

    release_files = {
        "test_release_schema_migration.py",
    }

    neo4j_ok = _neo4j_reachable()
    memgraph_ok = _memgraph_reachable()
    skip_memgraph_unreachable = pytest.mark.skip(
        reason="Memgraph unreachable (no server at MEMGRAPH_URL) — skipping 'slow' test"
    )
    skip_unreachable = pytest.mark.skip(
        reason="Neo4j unreachable (no server at NEO4J_DEV_URL/NEO4J_URL) — "
        "skipping 'slow' test instead of timing out on connection"
    )
    run_release = os.environ.get("CVCDOCDB_RUN_RELEASE_TESTS") == "1"
    skip_release = pytest.mark.skip(
        reason="release-only test (expensive cross-backend migration + "
        "schema codegen comparison) — set CVCDOCDB_RUN_RELEASE_TESTS=1 to "
        "run it. Intended for main, right before publishing a new version."
    )

    for item in items:
        filename = item.fspath.basename
        if filename in unit_files:
            item.add_marker(pytest.mark.unit)
        elif filename in nx_files:
            item.add_marker(pytest.mark.integration)
        elif filename in neo4j_files:
            item.add_marker(pytest.mark.slow)
        elif filename in release_files:
            item.add_marker(pytest.mark.release)
            item.add_marker(pytest.mark.slow)
        # test_graph_store_contract.py has individual markers on each method

        markers = {m.name for m in item.iter_markers()}
        if filename == "test_memgraph_graph.py" and "slow" in markers:
            if not memgraph_ok:
                item.add_marker(skip_memgraph_unreachable)
            elif not neo4j_ok and item.cls is not None and item.cls.__name__ == "MemgraphMatchesNeo4jTest":
                item.add_marker(skip_unreachable)
        elif not neo4j_ok and "slow" in markers:
            item.add_marker(skip_unreachable)
        if not run_release and "release" in markers:
            item.add_marker(skip_release)
