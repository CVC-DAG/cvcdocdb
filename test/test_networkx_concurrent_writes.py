"""Regression tests for cross-process lost updates in NetworkXGraph.

NetworkXGraph loads the whole persisted graph into memory once per
instance and, on every mutation, writes the whole in-memory graph back
to disk. Two instances (e.g. two processes) pointing at the same
persistence_path each hold an independent in-memory copy: if instance A
loads, then instance B writes and persists, then A performs its own
write, A's save (built from its now-stale in-memory copy) silently
erases B's already-persisted change — a classic lost update.

These tests simulate that scenario deterministically within one process
(two NetworkXGraph instances on the same path) and must pass once each
mutating call reloads the latest on-disk state before mutating, guarded
by a cross-process file lock.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from cvcdocdb.base import Node, Relation, WeakNode
from cvcdocdb.networkx_graph import NetworkXGraph


class LostUpdateAcrossInstancesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp_dir, "graph.pkl")

    def test_insert_does_not_erase_a_concurrent_instances_already_saved_insert(self) -> None:
        # A opens first (empty graph) — simulates a slow/long-lived instance.
        a = NetworkXGraph(persistence_path=self.path)

        # B opens later, inserts, and persists — simulates a concurrent
        # process that completed its write while A was still open.
        b = NetworkXGraph(persistence_path=self.path)
        b.insertNode(Node(pk={"id": "1"}, main_label="Thing", name="from b"))
        b.close()

        # A now performs its own insert. Without reloading first, A's
        # save would be built from its original (pre-B) empty snapshot
        # and would silently wipe out B's already-persisted node.
        a.insertNode(Node(pk={"id": "2"}, main_label="Thing", name="from a"))
        a.close()

        verifier = NetworkXGraph(persistence_path=self.path)
        results = verifier.query({"main_label": "Thing"})
        verifier.close()

        names = sorted(r["properties"].get("name") for r in results)
        self.assertEqual(names, ["from a", "from b"])

    def test_auto_generated_node_ids_do_not_collide_across_instances(self) -> None:
        """Node id assignment depends on an in-memory counter; it must be
        refreshed from disk too, or two instances can hand out the same id."""
        a = NetworkXGraph(persistence_path=self.path)
        b = NetworkXGraph(persistence_path=self.path)

        b.insertNode(Node(pk=None, main_label="Thing"))
        b.close()

        node_a = Node(pk=None, main_label="Thing")
        a.insertNode(node_a)
        a.close()

        verifier = NetworkXGraph(persistence_path=self.path)
        results = verifier.query({"main_label": "Thing"})
        verifier.close()

        # If the two instances' node-id counters collided, b's node would
        # have been overwritten/merged by a's insert instead of coexisting.
        self.assertEqual(len(results), 2)

    def test_create_group_is_still_atomic_and_single_saved(self) -> None:
        """create_group()'s internal insertNode/insertRelation calls must not
        each reload+save independently — that would still race with a
        concurrent writer between two of its own internal steps. Only the
        outermost create_group() call should reload once and save once."""
        graph = NetworkXGraph(persistence_path=self.path)
        strong = Node(pk={"id": "1"}, main_label="Doc")
        weak = WeakNode(pk={"n": 1}, main_label="Page", parent=strong, parent_relation="HAS_PAGE")
        graph.create_group(strong, weak_nodes=[weak])
        graph.close()

        reopened = NetworkXGraph(persistence_path=self.path)
        doc = reopened.query({"main_label": "Doc"})[0]
        page = reopened.query({"main_label": "Page"})[0]
        reopened.close()

        self.assertTrue(doc["properties"].get("_weak_init_done"))
        self.assertEqual(page["properties"].get("n"), 1)

    def test_delete_node_propagation_recursion_still_works_with_locking(self) -> None:
        """deleteNode() recurses into itself for cascade propagation; nested
        recursive calls must not reload mid-recursion and discard the outer
        call's not-yet-saved deletions."""
        graph = NetworkXGraph(persistence_path=self.path)
        parent = Node(pk={"id": "1"}, main_label="Doc")
        graph.insertNode(parent)
        child = WeakNode(pk={"n": 1}, main_label="Page", parent=parent, parent_relation="HAS_PAGE")
        graph.insertNode(child)

        graph.deleteNode(parent, propagation=True, detach=True)
        graph.close()

        reopened = NetworkXGraph(persistence_path=self.path)
        remaining = reopened.get_nodes()
        reopened.close()
        self.assertEqual(len(remaining), 0)


if __name__ == "__main__":
    unittest.main()
