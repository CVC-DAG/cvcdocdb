"""Bounded waits: NetworkXGraph's cross-process file lock and Neo4jGraph's
connection setup must be able to fail with a clear error instead of
waiting forever.

Regression for a real production hang: ``migrate()`` holds the source
``NetworkXGraph``'s file lock for the whole migration; when the Neo4j
target stopped answering mid-migration, the lock was never released and
every other writer of that ``.pkl`` (any process) blocked forever in
``FileLock.acquire()`` — which had no timeout at all.

Defaults are unchanged (``lock_timeout=None`` waits forever, and
``Neo4jGraph`` uses the driver's own defaults), so these are opt-in and
backward-compatible.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import pytest

from cvcdocdb import GraphLockTimeout
from cvcdocdb.base import Node
from cvcdocdb.migration import migrate
from cvcdocdb.networkx_graph import NetworkXGraph

pytestmark = pytest.mark.integration

LOCK_TIMEOUT_S = 0.5
# Marge generós per a màquines de CI lentes: l'important és que acabi,
# no la precisió exacta del temps.
TIMING_SLACK_S = 5.0
HOLDER_READY_MARKER = "LOCKED"


class _ExternalLockHolder:
    """Another *process* holding ``<path>.lock`` for ``hold_s`` seconds —
    the same situation as a stuck migration in a different process."""

    def __init__(self, path: str, hold_s: float) -> None:
        code = (
            "import sys, time\n"
            "from filelock import FileLock\n"
            f"lock = FileLock({path + '.lock'!r})\n"
            "lock.acquire()\n"
            f"print({HOLDER_READY_MARKER!r}, flush=True)\n"
            f"time.sleep({hold_s})\n"
            "lock.release()\n"
        )
        self.proc = subprocess.Popen(
            [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True
        )
        assert self.proc.stdout is not None
        assert self.proc.stdout.readline().strip() == HOLDER_READY_MARKER

    def stop(self) -> None:
        self.proc.kill()
        self.proc.wait()


class NetworkXLockTimeoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp_dir, "graph.pkl")
        seed = NetworkXGraph(persistence_path=self.path)
        seed.insertNode(Node(pk={"id": "seed"}, main_label="Thing"))
        seed.close()

    def _names(self) -> list:
        verifier = NetworkXGraph(persistence_path=self.path)
        try:
            return sorted(r["pk"]["id"] if r.get("pk") else r["properties"]["id"]
                          for r in verifier.query({"main_label": "Thing"}))
        finally:
            verifier.close()

    def test_write_raises_graph_lock_timeout_when_lock_is_held_elsewhere(self) -> None:
        holder = _ExternalLockHolder(self.path, hold_s=60)
        try:
            graph = NetworkXGraph(persistence_path=self.path, lock_timeout=LOCK_TIMEOUT_S)
            started = time.monotonic()
            with self.assertRaises(GraphLockTimeout) as ctx:
                graph.insertNode(Node(pk={"id": "blocked"}, main_label="Thing"))
            elapsed = time.monotonic() - started
            graph.close()
        finally:
            holder.stop()

        self.assertGreaterEqual(elapsed, LOCK_TIMEOUT_S)
        self.assertLess(elapsed, LOCK_TIMEOUT_S + TIMING_SLACK_S)
        self.assertIn(self.path, str(ctx.exception))
        self.assertNotIn("blocked", self._names())

    def test_graph_lock_timeout_is_a_timeout_error(self) -> None:
        self.assertTrue(issubclass(GraphLockTimeout, TimeoutError))

    def test_default_still_waits_until_the_lock_is_released(self) -> None:
        """Backward-compatible default: no timeout, the write just waits."""
        holder = _ExternalLockHolder(self.path, hold_s=1.0)
        try:
            graph = NetworkXGraph(persistence_path=self.path)
            graph.insertNode(Node(pk={"id": "after-wait"}, main_label="Thing"))
            graph.close()
        finally:
            holder.stop()
        self.assertIn("after-wait", self._names())

    def test_write_succeeds_when_lock_is_released_within_the_timeout(self) -> None:
        holder = _ExternalLockHolder(self.path, hold_s=0.3)
        try:
            graph = NetworkXGraph(persistence_path=self.path, lock_timeout=TIMING_SLACK_S)
            graph.insertNode(Node(pk={"id": "in-time"}, main_label="Thing"))
            graph.close()
        finally:
            holder.stop()
        self.assertIn("in-time", self._names())

    def test_nested_writes_inside_batch_do_not_time_out(self) -> None:
        """Reentrancy is unchanged: nested acquisitions by the same instance
        never wait on its own lock."""
        graph = NetworkXGraph(persistence_path=self.path, lock_timeout=LOCK_TIMEOUT_S)
        with graph.batch():
            graph.insertNode(Node(pk={"id": "n1"}, main_label="Thing"))
            graph.insertNode(Node(pk={"id": "n2"}, main_label="Thing"))
        graph.close()
        self.assertEqual(self._names(), ["n1", "n2", "seed"])

    def test_migrate_fails_fast_when_source_lock_is_held(self) -> None:
        holder = _ExternalLockHolder(self.path, hold_s=60)
        try:
            source = NetworkXGraph(persistence_path=self.path, lock_timeout=LOCK_TIMEOUT_S)
            target = NetworkXGraph(persistence_path=os.path.join(self.tmp_dir, "target.pkl"))
            with self.assertRaises(GraphLockTimeout):
                migrate(source, target)
            source.close()
            target.close()
        finally:
            holder.stop()

    def test_invalid_lock_timeout_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            NetworkXGraph(persistence_path=self.path, lock_timeout=-1)


class _SilentBoltServer:
    """Accepts TCP connections and never answers the Bolt handshake — a
    Neo4j host that is reachable but not responding."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._held: list = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                self._held.append(self.sock.accept())
            except OSError:
                return

    def close(self) -> None:
        for conn, _ in self._held:
            conn.close()
        self.sock.close()


class Neo4jDriverConfigTest(unittest.TestCase):
    def test_driver_config_is_forwarded_to_the_driver(self) -> None:
        from unittest import mock

        from cvcdocdb import neo4j_graph

        with mock.patch.object(neo4j_graph.GraphDatabase, "driver") as driver:
            driver.return_value.get_server_info.return_value.protocol_version = (5, 0)
            neo4j_graph.Neo4jGraph(
                "bolt://example:7687", "u", "p",
                connection_timeout=3.0, connection_acquisition_timeout=4.0,
            )
        _, kwargs = driver.call_args
        self.assertEqual(kwargs["connection_timeout"], 3.0)
        self.assertEqual(kwargs["connection_acquisition_timeout"], 4.0)
        self.assertEqual(kwargs["auth"], ("u", "p"))

    def test_unresponsive_server_fails_within_acquisition_timeout(self) -> None:
        # test_drm.py substitueix `neo4j` per un mock a sys.modules en
        # importar-se; amb un driver fals no hi ha res de real a cronometrar.
        if not getattr(sys.modules.get("neo4j"), "__file__", None):
            self.skipTest("neo4j is stubbed by another test module in this session")
        from neo4j.exceptions import ServiceUnavailable

        from cvcdocdb.neo4j_graph import Neo4jGraph

        server = _SilentBoltServer()
        acquisition_timeout_s = 1.0
        try:
            started = time.monotonic()
            with self.assertRaises(ServiceUnavailable):
                Neo4jGraph(
                    f"bolt://127.0.0.1:{server.port}", "u", "p",
                    connection_timeout=acquisition_timeout_s,
                    connection_acquisition_timeout=acquisition_timeout_s,
                )
            elapsed = time.monotonic() - started
        finally:
            server.close()
        self.assertLess(elapsed, acquisition_timeout_s + TIMING_SLACK_S)


if __name__ == "__main__":
    unittest.main()
