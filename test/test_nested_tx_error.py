"""Regression tests: a TransactionError/ConstraintError raised by a nested
insertNode/insertRelation/deleteNode/checkNode call (i.e. one running
inside someone else's already-open transaction — `graph.batch()`, or a
WeakNode's recursive `insertNode`) must NOT roll back, close or null the
*shared* `self._tx` — only the call that actually opened it (`inici=True`)
may do that. Before the fix, every one of these exception handlers reset
`self._tx = None` unconditionally, so a single failed insert inside a
`batch()` block corrupted the outer transaction: subsequent calls in the
same block silently opened+closed their own per-call transactions instead
of joining the batch, and `batch()`'s own `finally: self._tx.close()`
then raised `AttributeError: 'NoneType' object has no attribute 'close'`
(masking the real underlying error) — exactly what broke
`cvcdocdb.migration.migrate()` under `on_error="skip"` in production.

Requires a real Neo4j instance (NEO4J_DEV_* env vars) — same convention
as test_query_method.py::Neo4jQueryTest and test_neo4j_real.py.
"""

import os
import unittest
from unittest.mock import patch

from neo4j.exceptions import TransactionError

from cvcdocdb.base import Node, Relation
from cvcdocdb.neo4j_graph import Neo4jGraph

_TEST_LABEL = "TestNestedTxErrorNode"


class NestedTransactionErrorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.password = os.environ.get("NEO4J_DEV_PASSWORD", "")
        cls.graph = None
        if not cls.password:
            cls.skip_reason = "NEO4J_DEV_PASSWORD not set"
            return
        try:
            cls.graph = Neo4jGraph(
                url=os.environ.get("NEO4J_DEV_URL", "bolt://localhost:7687"),
                user=os.environ.get("NEO4J_DEV_USER", "neo4j"),
                password=cls.password,
                database=os.environ.get("NEO4J_DEV_DATABASE", "neo4j"),
            )
        except Exception as e:
            cls.skip_reason = f"Cannot connect to Neo4j: {e}"
            cls.graph = None

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.graph is not None:
            cls.graph.close()

    def _clean_test_nodes(self) -> None:
        for entry in self.graph.query({"main_label": _TEST_LABEL}):
            node_id = entry["properties"].get("id")
            if node_id is not None:
                self.graph.deleteNode(Node(pk={"id": node_id}, main_label=_TEST_LABEL), detach=True)

    def setUp(self) -> None:
        if self.graph is None:
            self.skipTest(self.skip_reason)
        if self.graph._tx is not None:
            try:
                self.graph._tx.close()
            except Exception:
                pass
            self.graph._tx = None
        # Neteja de residus d'una execució anterior interrompuda.
        self._clean_test_nodes()

    def tearDown(self) -> None:
        if self.graph is None:
            return
        if self.graph._tx is not None:
            try:
                self.graph._tx.close()
            except Exception:
                pass
            self.graph._tx = None
        self._clean_test_nodes()

    def _make_node(self, pk: int) -> Node:
        return Node(pk={"id": pk}, main_label=_TEST_LABEL)

    def test_nested_transaction_error_does_not_corrupt_batch_tx(self) -> None:
        """A TransactionError from a nested insertRelation (inici=False,
        running inside `batch()`'s transaction) must not touch `self._tx` —
        the exception propagates and `batch()` itself rolls back cleanly."""
        a = self._make_node(1)
        b = self._make_node(2)
        self.graph.insertNode(a)
        self.graph.insertNode(b)

        with patch.object(Neo4jGraph, "_create_relation", side_effect=TransactionError("simulated failure")):
            with self.assertRaises(TransactionError):
                with self.graph.batch():
                    tx_before = self.graph._tx
                    self.assertIsNotNone(tx_before)
                    try:
                        self.graph.insertRelation(Relation(a, b, "REL"))
                    finally:
                        # The nested call must leave the shared tx alone —
                        # this is the crux of the bug.
                        self.assertIs(self.graph._tx, tx_before)
                    # Not reached in this test, but if it were, batch()
                    # must still hold the very same tx object.

        # batch()'s own except/finally closed it after the propagated error.
        self.assertIsNone(self.graph._tx)

    def test_on_error_skip_style_recovery_inside_batch(self) -> None:
        """Mirrors cvcdocdb.migration.migrate(..., on_error="skip"): catch
        the nested failure ourselves and keep using the same batch — must
        not raise a secondary AttributeError on `batch()` exit, and the
        surviving insert must actually be committed."""
        a = self._make_node(3)
        b = self._make_node(4)
        c = self._make_node(5)
        self.graph.insertNode(a)
        self.graph.insertNode(b)
        self.graph.insertNode(c)

        real_create_relation = Neo4jGraph._create_relation
        calls = {"n": 0}

        def _flaky_create_relation(tx, rel):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TransactionError("simulated failure")
            return real_create_relation(tx, rel)

        with patch.object(Neo4jGraph, "_create_relation", side_effect=_flaky_create_relation):
            with self.graph.batch():
                try:
                    self.graph.insertRelation(Relation(a, b, "REL"))
                except TransactionError:
                    pass  # on_error="skip" semantics
                # This second call must still join (and complete) the same
                # batch transaction — not silently open/close its own.
                self.graph.insertRelation(Relation(a, c, "REL"))

        self.assertIsNone(self.graph._tx)
        result = self.graph.query(
            "MATCH (a:" + _TEST_LABEL + " {id: 3})-[r:REL]->(c:" + _TEST_LABEL + " {id: 5}) RETURN r"
        )
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
