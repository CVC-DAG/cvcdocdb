"""`migrate()` copia també les propietats de propagació.

Un graf amb WeakNodes, un `create_group()`, una relació que apunta a un
WeakNode i `init_propagation()` ja executat porta propietats de propagació
als nodes (`is_weak`, `_propagate`, `parent_relation`, `_weak_init_done`) i
a les relacions (`_propagate`, `parent_relation`). Migrar-lo a qualsevol
backend ha de deixar exactament el mateix graf i el mateix comportament en
esborrar. Abans:

- NetworkX → NetworkX perdia les propietats de propagació dels nodes
  (`Node()` les interpreta com a arguments estructurals o les descarta per
  començar amb `_`);
- NetworkX → Neo4j/Memgraph petava (un node `is_weak` sense pare);
- Memgraph com a origen petava (la lectura per lots només reconeixia
  `Neo4jGraph` pel nom de classe).

Es proven tots els parells de backends disponibles (NetworkX sempre;
Neo4j i Memgraph si hi ha servidor). Un mateix servidor no pot ser origen i
destí alhora.
"""

from __future__ import annotations

import os
import tempfile
import unittest
import warnings
from typing import Any, Callable, Dict, Optional

import pytest

from cvcdocdb.base import Node, Relation, WeakNode
from cvcdocdb.migration import migrate
from cvcdocdb.networkx_graph import NetworkXGraph

from test import propagation_scenarios as ps


def _networkx() -> NetworkXGraph:
    return NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "g.pkl"))


def _neo4j() -> Optional[Any]:
    from cvcdocdb.neo4j_graph import Neo4jGraph

    url = os.environ.get("NEO4J_DEV_URL")
    if not url:
        return None
    graph = Neo4jGraph(
        url, os.environ.get("NEO4J_DEV_USER", "neo4j"), os.environ.get("NEO4J_DEV_PASSWORD", ""),
        database=os.environ.get("NEO4J_DEV_DATABASE") or None,
    )
    graph.query("MATCH (n) DETACH DELETE n")
    return graph


def _memgraph() -> Optional[Any]:
    from cvcdocdb.memgraph_graph import MemgraphGraph

    url = os.environ.get("MEMGRAPH_URL")
    if not url:
        return None
    graph = MemgraphGraph(
        url, os.environ.get("MEMGRAPH_USER", ""), os.environ.get("MEMGRAPH_PASSWORD", ""),
        database=os.environ.get("MEMGRAPH_DATABASE") or None,
    )
    graph.query("MATCH (n) DETACH DELETE n")
    return graph


def _jena() -> Optional[Any]:
    url = os.environ.get("FUSEKI_URL")
    if not url:
        return None
    from test.test_jena_graph import _fresh_jena

    return _fresh_jena()


BACKENDS: Dict[str, Callable[[], Optional[Any]]] = {
    "networkx": _networkx, "neo4j": _neo4j, "memgraph": _memgraph, "jena": _jena,
}


def _build_propagation_graph(graph: Any) -> Node:
    """Graf amb totes les propietats de propagació. Retorna el `Reader`."""
    _, sec, page = ps.document_tree()
    graph.insertNode(page)
    group_root = Node(pk={"doc": "G1"}, main_label="Document")
    group_child = WeakNode(parent=group_root, pk={"sec": 9}, main_label="Section", parent_relation="HAS_SECTION")
    graph.create_group(group_root, weak_nodes=[group_child])
    reader = Node(pk={"id": 1}, main_label="Reader", name="r")
    graph.insertNode(reader)
    graph.insertRelation(Relation(reader, sec, "CITES"))
    graph.init_propagation()
    return reader


def _delete_reader_with_propagation(graph: Any) -> None:
    graph.deleteNode(Node(pk={"id": 1}, main_label="Reader", name="r"), propagation=True, detach=True)


class MigrationPropagationChecks:
    def check_pair(self, source_name: str, target_name: str) -> None:
        source = BACKENDS[source_name]()
        if source is None:
            self.skipTest(f"{source_name} no disponible")
        target = BACKENDS[target_name]()
        if target is None:
            source.close()
            self.skipTest(f"{target_name} no disponible")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                _build_propagation_graph(source)
                expected = ps.snapshot(source, ps.REPRESENTATION_KEYS)
                stats = migrate(source, target)
                self.assertEqual(stats.errors, [])
                self.assertEqual(ps.snapshot(target, ps.REPRESENTATION_KEYS), expected)
                # Mateix comportament en esborrar després de migrar.
                _delete_reader_with_propagation(source)
                _delete_reader_with_propagation(target)
                self.assertEqual(
                    ps.snapshot(target, ps.REPRESENTATION_KEYS), ps.snapshot(source, ps.REPRESENTATION_KEYS)
                )
        finally:
            source.close()
            target.close()


class NetworkXMigrationPropagationTest(MigrationPropagationChecks, unittest.TestCase):
    def test_networkx_to_networkx(self) -> None:
        self.check_pair("networkx", "networkx")


@pytest.mark.slow
class ServerMigrationPropagationTest(MigrationPropagationChecks, unittest.TestCase):
    def test_networkx_to_neo4j(self) -> None:
        self.check_pair("networkx", "neo4j")

    def test_networkx_to_memgraph(self) -> None:
        self.check_pair("networkx", "memgraph")

    def test_neo4j_to_networkx(self) -> None:
        self.check_pair("neo4j", "networkx")

    def test_neo4j_to_memgraph(self) -> None:
        self.check_pair("neo4j", "memgraph")

    def test_memgraph_to_networkx(self) -> None:
        self.check_pair("memgraph", "networkx")

    def test_memgraph_to_neo4j(self) -> None:
        self.check_pair("memgraph", "neo4j")

    def test_networkx_to_jena(self) -> None:
        self.check_pair("networkx", "jena")

    def test_jena_to_networkx(self) -> None:
        self.check_pair("jena", "networkx")

    def test_neo4j_to_jena(self) -> None:
        self.check_pair("neo4j", "jena")

    def test_jena_to_neo4j(self) -> None:
        self.check_pair("jena", "neo4j")

    def test_memgraph_to_jena(self) -> None:
        self.check_pair("memgraph", "jena")

    def test_jena_to_memgraph(self) -> None:
        self.check_pair("jena", "memgraph")

    def test_jena_to_jena(self) -> None:
        self.check_pair("jena", "jena")
