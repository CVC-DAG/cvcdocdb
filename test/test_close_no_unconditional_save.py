"""Regression test: NetworkXGraph.close() must never re-persist a stale
snapshot over another process/instance's concurrent write.

Found integrating cvcdocdb into a downstream app: `close()` used to call
`_save_state()` unconditionally, re-writing the *entire* persistence file
with whatever was loaded into memory when that instance was opened — even
for an instance that only ever did reads. Two overlapping opens of the
same path (e.g. a slow read in one process/request and a write in
another) would race: whichever instance closed last silently overwrote
the other's writes with its own (possibly stale) snapshot. This is
exactly what happened: 48 real nodes were reduced to 1 after nothing but
read-only queries ran concurrently with a write.
"""

import os
import tempfile
import unittest

from cvcdocdb.base import Node
from cvcdocdb.networkx_graph import NetworkXGraph


class CloseDoesNotClobberConcurrentWritesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp_dir, "graph.pkl")

    def test_read_only_instance_closing_after_a_concurrent_write_does_not_erase_it(self) -> None:
        # Instance A opens first (empty graph) — simulates a slow read.
        reader = NetworkXGraph(persistence_path=self.path)

        # Instance B opens later, writes a node, and closes — simulates a
        # concurrent request/process that mutates the graph.
        writer = NetworkXGraph(persistence_path=self.path)
        writer.insertNode(Node(pk={"id": "1"}, main_label="Thing", name="real data"))
        writer.close()

        # Instance A never mutated anything — just closes after doing reads.
        reader.query({"main_label": "Thing"})
        reader.close()

        # The writer's data must survive: a purely-reading instance closing
        # later must never overwrite it with its own (stale, pre-write) snapshot.
        verifier = NetworkXGraph(persistence_path=self.path)
        results = verifier.query({"main_label": "Thing"})
        verifier.close()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["properties"].get("name"), "real data")

    def test_mutations_are_still_persisted_across_close_and_reopen(self) -> None:
        """Sanity check: the fix doesn't break normal single-instance persistence."""
        graph = NetworkXGraph(persistence_path=self.path)
        graph.insertNode(Node(pk={"id": "1"}, main_label="Thing", name="hello"))
        graph.close()

        reopened = NetworkXGraph(persistence_path=self.path)
        results = reopened.query({"main_label": "Thing"})
        reopened.close()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["properties"].get("name"), "hello")

    def test_init_propagation_changes_are_still_persisted(self) -> None:
        """init_propagation() doesn't call _save_state() inline in the loop
        body — verify its own explicit save (added alongside this fix)
        still makes its changes survive close()+reopen."""
        from cvcdocdb.base import WeakNode

        graph = NetworkXGraph(persistence_path=self.path)
        parent = Node(pk={"id": "1"}, main_label="Doc")
        graph.insertNode(parent)
        child = WeakNode(pk={"n": 1}, main_label="Page", parent=parent, parent_relation="HAS_PAGE")
        graph.insertNode(child)
        graph.init_propagation()
        graph.close()

        reopened = NetworkXGraph(persistence_path=self.path)
        page = reopened.query({"main_label": "Page"})[0]
        reopened.close()

        self.assertTrue(page["properties"].get("is_weak"))


if __name__ == "__main__":
    unittest.main()
