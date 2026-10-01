"""Contracte de propagació comú a tots els backends.

1. **Atomicitat a NetworkX**: si una operació o un ``batch()`` falla, els
   canvis en memòria es desfan (abans no es desaven al disc però quedaven en
   memòria a mitges). Igual que una transacció de Neo4j.
2. **init_propagation()** (contracte documentat a ``docs/index.rst``,
   "Propagation Properties"): cada fill d'una relació ``_propagate`` d'un
   node pendent queda amb ``is_weak``, ``_propagate`` i ``parent_relation``
   (el tipus de la relació, al node); tots els nodes pendents queden amb
   ``_weak_init_done``. Ja no s'escriu ``_dependencies``: Neo4j no pot desar
   un dict com a propietat i NetworkX el calculava malament (comptava les
   relacions ``HAS_*`` dels WeakNodes com a dependències).
3. **create_group()**: com que marca el pare amb ``_weak_init_done``, deixa
   els fills inicialitzats igual que ho faria ``init_propagation()``, i la
   relació pare→fill porta ``_propagate`` i ``parent_relation``.

Els mateixos checks s'executen sobre NetworkX (sempre) i sobre Neo4j i
Memgraph (``slow``, si hi ha servidor).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any, Callable, Dict, List

import pytest

from cvcdocdb.base import Node, Relation, WeakNode
from cvcdocdb.networkx_graph import NetworkXGraph

from test import propagation_scenarios as ps


def _nodes_by_label(graph: Any) -> Dict[str, List[Dict[str, Any]]]:
    result: Dict[str, List[Dict[str, Any]]] = {}
    for row in graph.query("MATCH (n) RETURN labels(n) AS labels, properties(n) AS props"):
        for label in row["labels"]:
            result.setdefault(label, []).append(row["props"])
    return result


def _edges(graph: Any) -> List[Dict[str, Any]]:
    return [
        {"type": row["type"], **row["props"]}
        for row in graph.query("MATCH ()-[r]->() RETURN type(r) AS type, properties(r) AS props")
    ]


class PropagationContractChecks:
    """Checks del contracte; les subclasses donen `make_graph()`."""

    make_graph: Callable[[], Any]

    def setUp(self) -> None:
        self.graph = self.make_graph()

    def tearDown(self) -> None:
        self.graph.close()

    def test_init_propagation_marks_weak_children_and_processed_nodes(self) -> None:
        _, _, page = ps.document_tree()
        self.graph.insertNode(page)
        self.assertTrue(self.graph.init_propagation())
        nodes = _nodes_by_label(self.graph)
        for label, relation in (("Section", "HAS_SECTION"), ("Page", "HAS_PAGE")):
            props = nodes[label][0]
            self.assertIs(props.get("is_weak"), True, label)
            self.assertIs(props.get("_propagate"), True, label)
            self.assertEqual(props.get("parent_relation"), relation, label)
        root = nodes["Document"][0]
        self.assertNotIn("is_weak", root)
        self.assertNotIn("parent_relation", root)
        for label in ("Document", "Section", "Page"):
            self.assertIs(nodes[label][0].get("_weak_init_done"), True, label)

    def test_init_propagation_marks_any_unflagged_edge_into_a_weak_node(self) -> None:
        _, sec, page = ps.document_tree()
        self.graph.insertNode(page)
        reader = Node(pk={"id": 1}, main_label="Reader")
        self.graph.insertNode(reader)
        self.graph.insertRelation(Relation(reader, sec, "CITES"))
        self.graph.insertRelation(Relation(reader, page, "IGNORES", _propagate=False))
        self.graph.init_propagation()
        edges = {edge["type"]: edge for edge in _edges(self.graph)}
        self.assertIs(edges["CITES"].get("_propagate"), True)
        self.assertIs(edges["IGNORES"].get("_propagate"), False)

    def test_init_propagation_does_not_write_dependencies(self) -> None:
        _, _, page = ps.document_tree()
        self.graph.insertNode(page)
        self.graph.init_propagation()
        for props_list in _nodes_by_label(self.graph).values():
            for props in props_list:
                self.assertNotIn("_dependencies", props)

    def test_create_group_initializes_its_children(self) -> None:
        doc = Node(pk={"doc": "G1"}, main_label="Document")
        sec = WeakNode(parent=doc, pk={"sec": 1}, main_label="Section", parent_relation="HAS_SECTION")
        self.graph.create_group(doc, weak_nodes=[sec])
        nodes = _nodes_by_label(self.graph)
        self.assertIs(nodes["Document"][0].get("_weak_init_done"), True)
        section = nodes["Section"][0]
        self.assertIs(section.get("is_weak"), True)
        self.assertIs(section.get("_propagate"), True)
        self.assertEqual(section.get("parent_relation"), "HAS_SECTION")
        (edge,) = _edges(self.graph)
        self.assertEqual(edge, {"type": "HAS_SECTION", "_propagate": True, "parent_relation": "HAS_SECTION"})

    def test_init_propagation_after_create_group_changes_nothing_on_the_group(self) -> None:
        doc = Node(pk={"doc": "G1"}, main_label="Document")
        sec = WeakNode(parent=doc, pk={"sec": 1}, main_label="Section", parent_relation="HAS_SECTION")
        self.graph.create_group(doc, weak_nodes=[sec])
        before = _nodes_by_label(self.graph)
        self.graph.init_propagation()
        after = _nodes_by_label(self.graph)
        self.assertEqual(after["Document"], before["Document"])
        self.assertEqual(
            {k: v for k, v in after["Section"][0].items() if k != "_weak_init_done"},
            before["Section"][0],
        )


def _fresh_networkx() -> NetworkXGraph:
    return NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "g.pkl"))


class NetworkXPropagationContractTest(PropagationContractChecks, unittest.TestCase):
    make_graph = staticmethod(_fresh_networkx)


def _fresh_neo4j() -> Any:
    from cvcdocdb.neo4j_graph import Neo4jGraph

    url = os.environ.get("NEO4J_DEV_URL")
    if not url:
        raise unittest.SkipTest("Sense NEO4J_DEV_URL: cal un Neo4j real")
    graph = Neo4jGraph(
        url,
        os.environ.get("NEO4J_DEV_USER", "neo4j"),
        os.environ.get("NEO4J_DEV_PASSWORD", ""),
        database=os.environ.get("NEO4J_DEV_DATABASE") or None,
    )
    graph.query("MATCH (n) DETACH DELETE n")
    return graph


def _fresh_memgraph() -> Any:
    from cvcdocdb.memgraph_graph import MemgraphGraph

    url = os.environ.get("MEMGRAPH_URL")
    if not url:
        raise unittest.SkipTest("Sense MEMGRAPH_URL: cal un Memgraph real")
    graph = MemgraphGraph(
        url,
        os.environ.get("MEMGRAPH_USER", ""),
        os.environ.get("MEMGRAPH_PASSWORD", ""),
        database=os.environ.get("MEMGRAPH_DATABASE") or None,
    )
    graph.query("MATCH (n) DETACH DELETE n")
    return graph


@pytest.mark.slow
class Neo4jPropagationContractTest(PropagationContractChecks, unittest.TestCase):
    make_graph = staticmethod(_fresh_neo4j)


@pytest.mark.slow
class MemgraphPropagationContractTest(PropagationContractChecks, unittest.TestCase):
    make_graph = staticmethod(_fresh_memgraph)


class NetworkXAtomicWritesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.path = os.path.join(tempfile.mkdtemp(), "g.pkl")
        self.graph = NetworkXGraph(persistence_path=self.path)

    def _reopened(self) -> NetworkXGraph:
        return NetworkXGraph(persistence_path=self.path)

    def _fail_inside_batch(self) -> None:
        with self.assertRaises(RuntimeError):
            with self.graph.batch():
                self.graph.insertNode(Node(pk={"id": 2}, main_label="T"))
                self.graph.insertNode(Node(pk={"id": 3}, main_label="T"))
                self.graph.insertNode(Node(pk={"id": 2}, main_label="T"))  # duplicada

    def test_failed_batch_on_a_never_saved_graph_leaves_it_empty(self) -> None:
        self._fail_inside_batch()
        self.assertEqual(self.graph.get_node_ids(), [])
        self.assertEqual(self.graph.get_node_pks(), [])
        self.assertEqual(self._reopened().get_node_ids(), [])

    def test_failed_batch_restores_the_previous_state(self) -> None:
        self.graph.insertNode(Node(pk={"id": 1}, main_label="T", name="u"))
        self._fail_inside_batch()
        self.assertEqual(len(self.graph.get_node_ids()), 1)
        self.assertEqual(len(self.graph.get_node_pks()), 1)
        self.assertIsNone(self.graph.checkNode(Node(pk={"id": 2}, main_label="T")))
        self.assertEqual(len(self._reopened().get_node_ids()), 1)
        # El graf continua sent usable i coherent després del rollback.
        self.graph.insertNode(Node(pk={"id": 2}, main_label="T"))
        self.assertEqual(len(self.graph.get_node_ids()), 2)

    def test_failure_in_a_nested_batch_rolls_back_the_outer_one(self) -> None:
        with self.assertRaises(RuntimeError):
            with self.graph.batch():
                self.graph.insertNode(Node(pk={"id": 1}, main_label="T"))
                with self.graph.batch():
                    self.graph.insertNode(Node(pk={"id": 1}, main_label="T"))
        self.assertEqual(self.graph.get_node_ids(), [])

    def test_successful_batch_is_kept(self) -> None:
        with self.graph.batch():
            self.graph.insertNode(Node(pk={"id": 1}, main_label="T"))
            self.graph.insertNode(Node(pk={"id": 2}, main_label="T"))
        self.assertEqual(len(self._reopened().get_node_ids()), 2)
