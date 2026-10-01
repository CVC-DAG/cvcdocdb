"""Jerarquies de WeakNodes: inserció de tots els avantpassats i límit de
profunditat.

- `NetworkXGraph.insertNode(<WeakNode>)` ha d'inserir **tots** els
  avantpassats (no només el pare), cadascun amb la seva relació pare→fill
  `_propagate=TRUE`, com fan Neo4j i Memgraph. Abans, `Document → Section →
  Page` deixava el Document fora del graf.
- Un WeakNode sense pare (``insert_parent=False``) i un fill amb claus que
  no referencien les del pare es rebutgen abans d'escriure res, amb els
  mateixos errors que Neo4j.
- Profunditat màxima d'una cadena: `MAX_WEAK_CHAIN_DEPTH` = 3 nodes (arrel
  + 2 nivells de WeakNode). Més enllà, ara només s'avisa
  (`WeakNodeDepthWarning`); a la propera versió major aquests nodes tindran
  una surrogate key automàtica.

Els tests `slow` comparen NetworkX amb Neo4j amb els escenaris compartits de
`test/propagation_scenarios.py`.
"""

from __future__ import annotations

import os
import tempfile
import unittest
import warnings
from typing import Any

import pytest

from cvcdocdb.base import MAX_WEAK_CHAIN_DEPTH, Node, WeakNode, WeakNodeDepthWarning, weak_chain_depth
from cvcdocdb.networkx_graph import NetworkXGraph

from test import propagation_scenarios as ps


def _fresh_networkx() -> NetworkXGraph:
    return NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "g.pkl"))


def _labels(graph: NetworkXGraph) -> list:
    return sorted(graph.get_node_attrs(i)["main_label"] for i in graph.get_node_ids())


def _edges(graph: NetworkXGraph) -> list:
    return sorted(rel_type for _, _, rel_type in graph.get_edges())


class NetworkXWeakHierarchyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _fresh_networkx()

    def test_inserting_a_grandchild_inserts_every_ancestor(self) -> None:
        _, _, page = ps.document_tree()
        self.graph.insertNode(page)
        self.assertEqual(_labels(self.graph), ["Document", "Page", "Section"])
        self.assertEqual(_edges(self.graph), ["HAS_PAGE", "HAS_SECTION"])
        for src, dst, rel_type in self.graph.get_edges():
            self.assertEqual(self.graph.get_edge_attrs(src, dst, rel_type), {"_propagate": True})

    def test_siblings_share_their_ancestors(self) -> None:
        doc, sec, page = ps.document_tree()
        other_page = WeakNode(parent=sec, pk={"page": 8}, main_label="Page", parent_relation="HAS_PAGE")
        self.graph.insertNode(page)
        self.graph.insertNode(other_page)
        self.assertEqual(_labels(self.graph), ["Document", "Page", "Page", "Section"])
        self.assertEqual(_edges(self.graph), ["HAS_PAGE", "HAS_PAGE", "HAS_SECTION"])

    def test_child_of_an_already_inserted_parent(self) -> None:
        doc, sec, page = ps.document_tree()
        self.graph.insertNode(doc)
        self.graph.insertNode(sec, insert_parent=False)
        self.graph.insertNode(page, insert_parent=False)
        self.assertEqual(_labels(self.graph), ["Document", "Page", "Section"])
        self.assertEqual(_edges(self.graph), ["HAS_PAGE", "HAS_SECTION"])

    def test_missing_parent_is_refused_without_writing(self) -> None:
        _, sec, _ = ps.document_tree()
        with self.assertRaises(Exception) as caught:
            self.graph.insertNode(sec, insert_parent=False)
        self.assertTrue(str(caught.exception).startswith("CVCDocDB Exception: missing parent node"))
        self.assertEqual(self.graph.get_node_ids(), [])

    def test_child_keys_must_reference_parent_keys(self) -> None:
        doc, _, _ = ps.document_tree()
        self.graph.insertNode(doc)
        other = Node(pk={"doc": "D2"}, main_label="Document")
        sec = WeakNode(parent=other, pk={"sec": 1}, main_label="Section", parent_relation="HAS_SECTION")
        sec._parent = doc
        with self.assertRaises(RuntimeError) as caught:
            self.graph.insertNode(sec, insert_parent=False)
        self.assertIn("Integrity Constraint Violated", str(caught.exception))
        self.assertEqual(_labels(self.graph), ["Document"])

    def test_propagated_delete_of_the_root_removes_the_whole_chain(self) -> None:
        doc, _, page = ps.document_tree()
        self.graph.insertNode(page)
        self.graph.deleteNode(doc, propagation=True, detach=True)
        self.assertEqual(self.graph.get_node_ids(), [])

    def test_restrict_refuses_root_with_weak_descendants(self) -> None:
        doc, _, page = ps.document_tree()
        self.graph.insertNode(page)
        with self.assertRaises(RuntimeError):
            self.graph.deleteNode(doc)
        self.assertEqual(len(self.graph.get_node_ids()), 3)


class WeakChainDepthTest(unittest.TestCase):
    def test_max_depth_is_three_nodes(self) -> None:
        self.assertEqual(MAX_WEAK_CHAIN_DEPTH, 3)

    def test_depth_counts_nodes_from_the_root(self) -> None:
        doc, sec, page = ps.document_tree()
        self.assertEqual([weak_chain_depth(n) for n in (doc, sec, page)], [1, 2, 3])

    def test_chain_within_the_limit_does_not_warn(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ps.document_tree()
        self.assertEqual([w for w in caught if issubclass(w.category, WeakNodeDepthWarning)], [])

    def test_deeper_chain_warns_about_surrogate_keys(self) -> None:
        _, _, page = ps.document_tree()
        with self.assertWarns(WeakNodeDepthWarning) as caught:
            WeakNode(parent=page, pk={"line": 1}, main_label="Line", parent_relation="HAS_LINE")
        message = str(caught.warning)
        self.assertIn("surrogate key", message)
        self.assertIn("Line", message)
        self.assertTrue(issubclass(WeakNodeDepthWarning, FutureWarning))

    def test_deeper_chain_still_works_for_now(self) -> None:
        graph = _fresh_networkx()
        _, _, page = ps.document_tree()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", WeakNodeDepthWarning)
            line = WeakNode(parent=page, pk={"line": 1}, main_label="Line", parent_relation="HAS_LINE")
        graph.insertNode(line)
        self.assertEqual(_labels(graph), ["Document", "Line", "Page", "Section"])
        self.assertEqual(line["pk_attributes"], {"doc": "D1", "sec": 1, "page": 7, "line": 1})




def _fresh_neo4j() -> Any:
    from cvcdocdb.neo4j_graph import Neo4jGraph

    graph = Neo4jGraph(
        os.environ["NEO4J_DEV_URL"],
        os.environ.get("NEO4J_DEV_USER", "neo4j"),
        os.environ.get("NEO4J_DEV_PASSWORD", ""),
        database=os.environ.get("NEO4J_DEV_DATABASE") or None,
    )
    graph.query("MATCH (n) DETACH DELETE n")
    return graph


@pytest.mark.slow
class NetworkXMatchesNeo4jTest(unittest.TestCase):
    """La política de propagació de NetworkX és la de Neo4j."""

    def test_every_scenario_matches_neo4j(self) -> None:
        if not os.environ.get("NEO4J_DEV_URL"):
            self.skipTest("Sense NEO4J_DEV_URL: no hi ha Neo4j per comparar")
        for scenario in ps.SCENARIOS:
            with self.subTest(scenario=scenario.__name__):
                expected = ps.run_scenario(_fresh_neo4j, scenario, ignore=ps.REPRESENTATION_KEYS)
                actual = ps.run_scenario(_fresh_networkx, scenario, ignore=ps.REPRESENTATION_KEYS)
                self.assertEqual(actual, expected)
