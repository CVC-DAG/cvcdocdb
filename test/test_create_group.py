"""Tests for create_group (transactional atomic creation) and
init_propagation (lazy background property initialization).
"""

import threading
import time
import unittest
from typing import Any, Dict, List, Optional, Tuple

import pytest

from cvcdocdb.base import Node, Relation, WeakNode, WeakRelation
from cvcdocdb.networkx_graph import NetworkXGraph


# ======================================================================
# create_group tests — NetworkXGraph
# ======================================================================


class TestCreateGroupNetworkX(unittest.TestCase):
    """Test create_group atomicity and correctness on NetworkXGraph."""

    def setUp(self) -> None:
        # Use a unique temp path to avoid loading stale state from previous runs
        import tempfile
        path = tempfile.mktemp(suffix=".pkl")
        self.graph = NetworkXGraph(persistence_path=path)

    def tearDown(self) -> None:
        self.graph.close()
        import os
        if os.path.exists(self.graph._persistence_path):
            os.unlink(self.graph._persistence_path)

    def test_create_group_single_strong_node(self) -> None:
        """A group with only a strong node is created."""
        doc = Node(pk={"id": "DOC-1"}, main_label="Document")
        nid = self.graph.create_group(doc)
        self.assertIsNotNone(nid)
        self.assertEqual(self.graph.count(), 1)
        # Verify the node exists with correct label
        results = self.graph.query({"main_label": "Document"})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["properties"]["pk"], {"id": "DOC-1"})

    def test_create_group_with_weak_nodes(self) -> None:
        """A group with a strong node and its WeakNodes is created."""
        doc = Node(pk={"id": "DOC-2"}, main_label="Document")
        page1 = WeakNode(
            parent=doc,
            parent_relation="HAS_PAGE",
            pk={"page": 1},
            main_label="Page",
        )
        page1["content"] = "First page"
        pages = [page1]

        nid = self.graph.create_group(doc, weak_nodes=pages)
        self.assertIsNotNone(nid)
        self.assertEqual(self.graph.count(), 2)

        # Verify both nodes exist
        docs = self.graph.query({"main_label": "Document"})
        pages_q = self.graph.query({"main_label": "Page"})
        self.assertEqual(len(docs), 1)
        self.assertEqual(len(pages_q), 1)
        self.assertEqual(pages_q[0]["properties"]["page"], 1)

    def test_create_group_with_weak_relations(self) -> None:
        """A group with weak relations linking parent to weak nodes."""
        doc = Node(pk={"id": "DOC-3"}, main_label="Document")
        section = WeakNode(
            parent=doc,
            parent_relation="HAS_SECTION",
            pk={"section": "intro"},
            main_label="Section",
        )
        sections = [section]
        rels: List[Relation] = []

        nid = self.graph.create_group(doc, weak_nodes=sections, weak_relations=rels)
        self.assertIsNotNone(nid)
        self.assertEqual(self.graph.count(), 2)

    def test_create_group_rollback_on_failure(self) -> None:
        """If any node fails, the entire group is rolled back."""
        doc = Node(pk={"id": "DOC-4"}, main_label="Document")
        # First create the same document to create a duplicate
        self.graph.insertNode(doc)

        # Now try to create the same group again — should fail and rollback
        doc2 = Node(pk={"id": "DOC-4"}, main_label="Document")
        page = WeakNode(
            parent=doc2,
            parent_relation="HAS_PAGE",
            pk={"page": 1},
            main_label="Page",
        )
        with self.assertRaises(RuntimeError):
            self.graph.create_group(doc2, weak_nodes=[page])

        # Graph should still have only 1 node (the original document)
        self.assertEqual(self.graph.count(), 1)

    def test_create_group_sets_propagate_on_edges(self) -> None:
        """WeakNode edges get _propagate=True set."""
        doc = Node(pk={"id": "DOC-5"}, main_label="Document")
        page = WeakNode(
            parent=doc,
            parent_relation="HAS_PAGE",
            pk={"page": 1},
            main_label="Page",
        )
        self.graph.create_group(doc, weak_nodes=[page])

        # Check edge attrs for _propagate
        edges = self.graph.get_edges()
        self.assertEqual(len(edges), 1)
        src, dst, rel_type = edges[0]
        edge_attrs = self.graph.get_edge_attrs(src, dst, rel_type)
        self.assertIsNotNone(edge_attrs)
        self.assertTrue(edge_attrs.get("_propagate"))

    def test_create_group_sets_weak_init_done(self) -> None:
        """Strong node gets _weak_init_done=True after create_group."""
        doc = Node(pk={"id": "DOC-6"}, main_label="Document")
        page = WeakNode(
            parent=doc,
            parent_relation="HAS_PAGE",
            pk={"page": 1},
            main_label="Page",
        )
        self.graph.create_group(doc, weak_nodes=[page])

        # Check node attrs for _weak_init_done
        doc_attrs = None
        for nid, attrs in self.graph._node_attrs.items():
            if attrs.get("main_label") == "Document":
                doc_attrs = attrs
                break
        self.assertIsNotNone(doc_attrs)
        self.assertTrue(doc_attrs.get("_weak_init_done"))

    def test_create_group_weak_init_done_rollback(self) -> None:
        """_weak_init_done is NOT set if create_group rolls back."""
        doc = Node(pk={"id": "DOC-7"}, main_label="Document")
        self.graph.insertNode(doc)

        doc2 = Node(pk={"id": "DOC-7"}, main_label="Document")
        page = WeakNode(
            parent=doc2,
            parent_relation="HAS_PAGE",
            pk={"page": 1},
            main_label="Page",
        )
        with self.assertRaises(RuntimeError):
            self.graph.create_group(doc2, weak_nodes=[page])

        # No _weak_init_done should be set (rollback happened)
        for nid, attrs in self.graph._node_attrs.items():
            self.assertIsNone(attrs.get("_weak_init_done"))


# ======================================================================
# init_propagation tests — NetworkXGraph
# ======================================================================


class TestInitPropagationNetworkX(unittest.TestCase):
    """Test init_propagation on NetworkXGraph."""

    def setUp(self) -> None:
        import tempfile
        path = tempfile.mktemp(suffix=".pkl")
        self.graph = NetworkXGraph(persistence_path=path)

    def tearDown(self) -> None:
        self.graph.close()
        import os
        if os.path.exists(self.graph._persistence_path):
            os.unlink(self.graph._persistence_path)

    def test_init_propagation_marks_weak_nodes(self) -> None:
        """Nodes connected via _propagate edges are marked is_weak."""
        doc = Node(pk={"id": "DOC-10"}, main_label="Document")
        page = WeakNode(
            parent=doc,
            parent_relation="HAS_PAGE",
            pk={"page": 1},
            main_label="Page",
        )
        self.graph.create_group(doc, weak_nodes=[page])

        # Before init: page should not have is_weak in attrs
        page_id = None
        for nid, attrs in self.graph._node_attrs.items():
            if attrs.get("main_label") == "Page":
                page_id = nid
                break
        self.assertIsNotNone(page_id)

        # Run init
        result = self.graph.init_propagation()
        self.assertTrue(result)

        # After init: page should have is_weak=True
        page_attrs = self.graph._node_attrs.get(page_id, {})
        self.assertTrue(page_attrs.get("is_weak"))

    def test_init_propagation_idempotent(self) -> None:
        """Second call returns False (already initialized)."""
        doc = Node(pk={"id": "DOC-11"}, main_label="Document")
        self.graph.create_group(doc)

        self.graph.init_propagation()
        result = self.graph.init_propagation()
        self.assertFalse(result)

    def test_init_propagation_with_background(self) -> None:
        """Background mode starts a thread and returns True."""
        doc = Node(pk={"id": "DOC-12"}, main_label="Document")
        page = WeakNode(
            parent=doc,
            parent_relation="HAS_PAGE",
            pk={"page": 1},
            main_label="Page",
        )
        self.graph.create_group(doc, weak_nodes=[page])

        result = self.graph.init_propagation(background=True)
        self.assertTrue(result)
        # Wait for background thread to finish
        time.sleep(0.5)

        # Should be marked
        for nid, attrs in self.graph._node_attrs.items():
            if attrs.get("main_label") == "Page":
                self.assertTrue(attrs.get("is_weak"))
                break

    def test_init_propagation_with_progress_callback(self) -> None:
        """Progress callback is called during scan."""
        doc = Node(pk={"id": "DOC-13"}, main_label="Document")
        page = WeakNode(
            parent=doc,
            parent_relation="HAS_PAGE",
            pk={"page": 1},
            main_label="Page",
        )
        self.graph.create_group(doc, weak_nodes=[page])

        calls: List[Tuple[int, int]] = []
        def cb(processed: int, total: int) -> None:
            calls.append((processed, total))

        self.graph.init_propagation(progress_callback=cb)
        self.assertTrue(len(calls) > 0)
        # Last call should show completion
        self.assertEqual(calls[-1][0], calls[-1][1])


# ======================================================================
# create_group tests — Neo4jGraph (contract test style)
# ======================================================================


class TestCreateGroupNeo4jContract(unittest.TestCase):
    """Test create_group contract on Neo4jGraph.

    These tests use the Neo4jGraph interface and verify the method
    signature and basic behavior. They require a running Neo4j instance
    for full validation.
    """

    def test_create_group_signature(self) -> None:
        """create_group exists on Neo4jGraph and has the right signature."""
        from cvcdocdb.neo4j_graph import Neo4jGraph

        # Just verify the method exists and is callable
        self.assertTrue(hasattr(Neo4jGraph, "create_group"))
        import inspect
        sig = inspect.signature(Neo4jGraph.create_group)
        params = list(sig.parameters.keys())
        self.assertIn("strong_node", params)
        self.assertIn("weak_nodes", params)
        self.assertIn("weak_relations", params)

    def test_init_propagation_signature(self) -> None:
        """init_propagation exists on Neo4jGraph and has the right signature."""
        from cvcdocdb.neo4j_graph import Neo4jGraph

        self.assertTrue(hasattr(Neo4jGraph, "init_propagation"))
        import inspect
        sig = inspect.signature(Neo4jGraph.init_propagation)
        params = list(sig.parameters.keys())
        self.assertIn("background", params)
        self.assertIn("progress_callback", params)


# ======================================================================
# GraphStore ABC contract test
# ======================================================================


class TestGraphStoreContract(unittest.TestCase):
    """Verify that both backends implement the new abstract methods."""

    def test_graph_store_has_create_group(self) -> None:
        from cvcdocdb.graph_store import GraphStore
        self.assertTrue(hasattr(GraphStore, "create_group"))

    def test_graph_store_has_init_propagation(self) -> None:
        from cvcdocdb.graph_store import GraphStore
        self.assertTrue(hasattr(GraphStore, "init_propagation"))

    def test_networkx_implements_create_group(self) -> None:
        """NetworkXGraph.create_group is not the ABC stub."""
        import tempfile
        from cvcdocdb.networkx_graph import NetworkXGraph
        path = tempfile.mktemp(suffix=".pkl")
        graph = NetworkXGraph(persistence_path=path)
        try:
            doc = Node(pk={"id": "TEST-1"}, main_label="Test")
            nid = graph.create_group(doc)
            self.assertIsNotNone(nid)
        finally:
            graph.close()
            import os
            if os.path.exists(path):
                os.unlink(path)

    def test_networkx_implements_init_propagation(self) -> None:
        """NetworkXGraph.init_propagation is not the ABC stub."""
        import tempfile
        from cvcdocdb.networkx_graph import NetworkXGraph
        path = tempfile.mktemp(suffix=".pkl")
        graph = NetworkXGraph(persistence_path=path)
        try:
            doc = Node(pk={"id": "TEST-2"}, main_label="Test")
            graph.insertNode(doc)
            result = graph.init_propagation()
            self.assertTrue(result)
        finally:
            graph.close()
            import os
            if os.path.exists(path):
                os.unlink(path)


if __name__ == "__main__":
    unittest.main()


# ======================================================================
# create_group with nested WeakNodes (Document → Section → Page)
# ======================================================================


def _nested_group(doc_id: str) -> Tuple[Node, WeakNode, WeakNode]:
    doc = Node(pk={"doc_id": doc_id}, main_label="Document")
    sec = WeakNode(parent=doc, parent_relation="HAS_SECTION", pk={"section": "intro"}, main_label="Section")
    page = WeakNode(parent=sec, parent_relation="HAS_PAGE", pk={"page": 1}, main_label="Page")
    return doc, sec, page


# Each WeakNode hangs from its own parent: the page from the section.
EXPECTED_NESTED_EDGES = {
    ("Document", "HAS_SECTION", "Section", True, "HAS_SECTION"),
    ("Section", "HAS_PAGE", "Page", True, "HAS_PAGE"),
}


class TestCreateGroupNestedNetworkX(unittest.TestCase):
    """create_group links each WeakNode to its own parent on NetworkXGraph."""

    def setUp(self) -> None:
        import os
        import tempfile

        self.graph = NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "nested.pkl"))

    def tearDown(self) -> None:
        self.graph.close()

    def _edges(self) -> set:
        label = lambda nid: self.graph.get_node_attrs(nid)["main_label"]  # noqa: E731
        edges = set()
        for src, dst, rel_type in self.graph.get_edges():
            attrs = self.graph.get_edge_attrs(src, dst, rel_type)
            edges.add((label(src), rel_type, label(dst), attrs.get("_propagate"), attrs.get("parent_relation")))
        return edges

    def test_nested_weak_node_is_linked_to_its_own_parent(self) -> None:
        doc, sec, page = _nested_group("NESTED-NX")
        self.graph.create_group(doc, weak_nodes=[sec, page])
        self.assertEqual(self.graph.count(), 3)
        self.assertEqual(self._edges(), EXPECTED_NESTED_EDGES)

    def test_nested_weak_nodes_are_initialised(self) -> None:
        doc, sec, page = _nested_group("NESTED-NX")
        doc_id = self.graph.create_group(doc, weak_nodes=[sec, page])
        for node, rel_type in ((sec, "HAS_SECTION"), (page, "HAS_PAGE")):
            attrs = self.graph.get_node_attrs(self.graph.checkNode(node))
            self.assertTrue(attrs["is_weak"])
            self.assertEqual(attrs["parent_relation"], rel_type)
        self.assertTrue(self.graph.get_node_attrs(doc_id)["_weak_init_done"])

    def test_nested_group_subdocuments_and_propagated_delete(self) -> None:
        doc, sec, page = _nested_group("NESTED-NX")
        self.graph.create_group(doc, weak_nodes=[sec, page])
        labels = sorted(sd["label"] for sd in self.graph.get_subdocuments(doc))
        self.assertEqual(labels, ["Page", "Section"])
        self.graph.deleteNode(sec, propagation=True, detach=True)
        self.assertIsNone(self.graph.checkNode(page))
        self.assertIsNotNone(self.graph.checkNode(doc))


    def test_children_may_come_before_their_parents(self) -> None:
        doc, sec, page = _nested_group("NESTED-NX")
        self.graph.create_group(doc, weak_nodes=[page, sec])
        self.assertEqual(self._edges(), EXPECTED_NESTED_EDGES)

    def test_parent_outside_the_group_is_refused_without_writing(self) -> None:
        doc, sec, page = _nested_group("NESTED-NX")
        with self.assertRaises(ValueError):
            self.graph.create_group(doc, weak_nodes=[page])  # its section is missing
        self.assertEqual(self.graph.count(), 0)


@pytest.mark.slow
class TestCreateGroupNestedNeo4j(unittest.TestCase):
    """create_group links each WeakNode to its own parent on a real Neo4j
    (needs NEO4J_DEV_URL). Only touches the nodes of its own document."""

    def setUp(self) -> None:
        import os
        import uuid

        if not os.environ.get("NEO4J_DEV_URL"):
            self.skipTest("Sense NEO4J_DEV_URL: cal un Neo4j real")
        from cvcdocdb.neo4j_graph import Neo4jGraph

        self.graph = Neo4jGraph(
            os.environ["NEO4J_DEV_URL"],
            os.environ.get("NEO4J_DEV_USER", "neo4j"),
            os.environ.get("NEO4J_DEV_PASSWORD", ""),
            database=os.environ.get("NEO4J_DEV_DATABASE") or None,
        )
        self.doc_id = f"NESTED-{uuid.uuid4().hex}"

    def tearDown(self) -> None:
        self.graph.query("MATCH (n) WHERE n.doc_id = $id DETACH DELETE n", params={"id": self.doc_id})
        self.graph.close()

    def test_nested_weak_node_is_linked_to_its_own_parent(self) -> None:
        doc, sec, page = _nested_group(self.doc_id)
        self.graph.create_group(doc, weak_nodes=[sec, page])
        rows = self.graph.query(
            "MATCH (a)-[r]->(b) WHERE a.doc_id = $id AND b.doc_id = $id "
            "RETURN labels(a)[0] AS a, type(r) AS t, labels(b)[0] AS b, "
            "r._propagate AS propagate, r.parent_relation AS parent_relation",
            params={"id": self.doc_id},
        )
        edges = {(r["a"], r["t"], r["b"], r["propagate"], r["parent_relation"]) for r in rows}
        self.assertEqual(edges, EXPECTED_NESTED_EDGES)
