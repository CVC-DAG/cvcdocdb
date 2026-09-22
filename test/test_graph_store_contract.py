"""Contract tests for GraphStore propagation policies.

These tests verify that **any** GraphStore implementation behaves
identically with respect to the propagation / deletion / update
policies.  Each concrete backend gets its own test class.

Usage::

    # For NetworkXGraph:
    python -m unittest test.test_graph_store_contract.TestNetworkXGraph

    # For Neo4jGraph (requires a real Neo4j database):
    export NEO4J_DEV_URL=bolt://localhost:7687
    export NEO4J_DEV_USER=neo4j
    export NEO4J_DEV_PASSWORD=secret
    python -m unittest test.test_graph_store_contract.TestNeo4jGraph
"""

from __future__ import annotations

import os
import pytest
import tempfile
import unittest
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from cvcdocdb.base import Node, Relation, WeakNode
from cvcdocdb.graph_store import GraphStore

if TYPE_CHECKING:
    pass  # Avoid circular imports at runtime


# ======================================================================
# Test helpers — build graph state for any GraphStore
# ======================================================================


def _setup_parent_graph(graph: GraphStore) -> Tuple[Node, Node, Node]:
    """Create a graph with three nodes and two edges: A→B, A→C."""
    a = Node(pk={"id": 1}, main_label="TestNode")
    b = Node(pk={"id": 2}, main_label="TestNode")
    c = Node(pk={"id": 3}, main_label="TestNode")
    graph.insertNode(a, replace=True)
    graph.insertNode(b, replace=True)
    graph.insertNode(c, replace=True)
    graph.insertRelation(Relation(a, b, "LINKS"))
    graph.insertRelation(Relation(a, c, "LINKS"))
    return a, b, c


def _setup_chain_graph(graph: GraphStore) -> Tuple[Node, Node, Node]:
    """Create a graph with a chain: A→B→C."""
    a = Node(pk={"id": 1}, main_label="TestNode")
    b = Node(pk={"id": 2}, main_label="TestNode")
    c = Node(pk={"id": 3}, main_label="TestNode")
    graph.insertNode(a, replace=True)
    graph.insertNode(b, replace=True)
    graph.insertNode(c, replace=True)
    graph.insertRelation(Relation(a, b, "LINKS"))
    graph.insertRelation(Relation(b, c, "LINKS"))
    return a, b, c


# ======================================================================
# Contract tests — run against any GraphStore implementation
# ======================================================================


class TestNetworkXGraph(unittest.TestCase):
    """Run all GraphStore contract tests against NetworkXGraph."""

    def setUp(self) -> None:
        self._persistence_path = os.path.join(tempfile.gettempdir(), "drm_tools_networkx_contract_state.pkl")
        if os.path.exists(self._persistence_path):
            os.remove(self._persistence_path)

    def tearDown(self) -> None:
        if os.path.exists(self._persistence_path):
            os.remove(self._persistence_path)

    def _make_graph(self) -> GraphStore:
        from cvcdocdb.networkx_graph import NetworkXGraph
        return NetworkXGraph(persistence_path=self._persistence_path)

    # -- ON DELETE RESTRICT --

    @pytest.mark.integration
    def test_contract_delete_restrict_refuses_with_edges(self) -> None:
        """ON DELETE RESTRICT: no es pot esborrar un node amb arestes."""
        graph = self._make_graph()
        a, b, c = _setup_parent_graph(graph)
        with self.assertRaises(RuntimeError) as ctx:
            graph.deleteNode(a, detach=False)
        self.assertIn("ON DELETE RESTRICT", str(ctx.exception))
        graph.close()

    @pytest.mark.integration
    def test_contract_delete_restrict_succeeds_without_edges(self) -> None:
        """ON DELETE RESTRICT: es pot esborrar un node sense arestes."""
        graph = self._make_graph()
        a = Node(pk={"id": 1}, main_label="TestNode")
        graph.insertNode(a, replace=True)
        result = graph.deleteNode(a, detach=False)
        self.assertTrue(result)
        graph.close()

    # -- ON DELETE CASCADE --

    @pytest.mark.integration
    def test_contract_delete_cascade_removes_edges(self) -> None:
        """ON DELETE CASCADE: esborra node + arestes."""
        graph = self._make_graph()
        a, b, c = _setup_parent_graph(graph)
        result = graph.deleteNode(a, detach=True)
        self.assertTrue(result)
        self.assertEqual(len(graph.get_node_ids()), 2)  # b, c queden
        self.assertEqual(len(graph.get_edges()), 0)  # totes les arestes esborrades
        graph.close()

    @pytest.mark.integration
    def test_contract_delete_cascade_leaves_orphans(self) -> None:
        """ON DELETE CASCADE: els veïns queden com a orfes (NO cascada).

        Aquest és el punt més important: CASCADE esborra node + arestes,
        però NO esborra els nodes veïns.
        """
        graph = self._make_graph()
        a, b, c = _setup_parent_graph(graph)
        graph.deleteNode(a, detach=True)
        # b i c queden al graf com a nodes independents
        pks = [p["pk"] for p in graph.get_node_pks()]
        self.assertIn({"id": 2}, pks)
        pks = [p["pk"] for p in graph.get_node_pks()]
        self.assertIn({"id": 3}, pks)
        graph.close()

    @pytest.mark.integration
    def test_contract_delete_cascade_chain(self) -> None:
        """ON DELETE CASCADE en cadena A→B→C: esborrar A no esborra B ni C."""
        graph = self._make_graph()
        a, b, c = _setup_chain_graph(graph)
        graph.deleteNode(a, detach=True)
        self.assertEqual(len(graph.get_node_ids()), 2)  # b, c queden
        self.assertEqual(len(graph.get_edges()), 1)  # B→C es manté
        graph.close()

    # -- ON DELETE SET NULL --

    @pytest.mark.integration
    def test_contract_delete_set_null_keeps_neighbors(self) -> None:
        """ON DELETE SET NULL: esborra node però manté veïns (sense cascada)."""
        graph = self._make_graph()
        a, b, c = _setup_parent_graph(graph)
        graph.deleteNode(a, detach=True, on_delete="set_null")
        self.assertEqual(len(graph.get_node_ids()), 2)  # b, c queden
        self.assertEqual(len(graph.get_edges()), 0)  # arestes esborrades
        pks = [p["pk"] for p in graph.get_node_pks()]
        self.assertIn({"id": 2}, pks)
        pks = [p["pk"] for p in graph.get_node_pks()]
        self.assertIn({"id": 3}, pks)
        graph.close()

    @pytest.mark.integration
    def test_contract_delete_set_null_no_cascade_to_neighbors(self) -> None:
        """ON DELETE SET NULL: esborrar node del mig no cascada als veïns."""
        graph = self._make_graph()
        a, b, c = _setup_chain_graph(graph)
        graph.deleteNode(b, detach=True, on_delete="set_null")
        # a i c queden (no cascada)
        self.assertEqual(len(graph.get_node_ids()), 2)
        pks = [p["pk"] for p in graph.get_node_pks()]
        self.assertIn({"id": 1}, pks)
        pks = [p["pk"] for p in graph.get_node_pks()]
        self.assertIn({"id": 3}, pks)
        graph.close()

    # -- ON UPDATE CASCADE --

    @pytest.mark.integration
    def test_contract_update_preserves_edges(self) -> None:
        """ON UPDATE CASCADE: actualitzar un node no trenca les arestes."""
        graph = self._make_graph()
        a, b, c = _setup_parent_graph(graph)
        a_updated = Node(pk={"id": 1}, main_label="TestNode", updated="yes")
        graph.insertNode(a_updated, update=True)
        self.assertEqual(len(graph.get_node_ids()), 3)
        self.assertEqual(len(graph.get_edges()), 2)  # les arestes es mantenen
        graph.close()

    # -- REPLACE --

    @pytest.mark.integration
    def test_contract_replace_removes_edges(self) -> None:
        """Replace: esborra node amb detach, les arestes s'esborren."""
        graph = self._make_graph()
        a, b, c = _setup_parent_graph(graph)
        a_new = Node(pk={"id": 1}, main_label="TestNode", replaced="yes")
        graph.insertNode(a_new, replace=True)
        self.assertEqual(len(graph.get_node_ids()), 3)  # a nou + b + c
        self.assertEqual(len(graph.get_edges()), 0)  # les arestes antigues s'esborren
        graph.close()

    # -- FK VIOLATION --

    @pytest.mark.integration
    def test_contract_fk_violation_src_missing(self) -> None:
        """FK violation: crear relació amb src no inserit llança RuntimeError."""
        graph = self._make_graph()
        src = Node(pk={"id": 999}, main_label="MissingNode")
        dst = Node(pk={"id": 1}, main_label="TestNode")
        graph.insertNode(dst, replace=True)
        rel = Relation(src, dst, "CONNECTS")
        with self.assertRaises(RuntimeError) as ctx:
            graph.insertRelation(rel)
        self.assertIn("FK violation", str(ctx.exception))
        self.assertIn("src", str(ctx.exception))
        graph.close()

    @pytest.mark.integration
    def test_contract_fk_violation_dst_missing(self) -> None:
        """FK violation: crear relació amb dst no inserit llança RuntimeError."""
        graph = self._make_graph()
        src = Node(pk={"id": 1}, main_label="TestNode")
        dst = Node(pk={"id": 999}, main_label="MissingNode")
        graph.insertNode(src, replace=True)
        rel = Relation(src, dst, "CONNECTS")
        with self.assertRaises(RuntimeError) as ctx:
            graph.insertRelation(rel)
        self.assertIn("FK violation", str(ctx.exception))
        self.assertIn("dst", str(ctx.exception))
        graph.close()

    # -- WEAK NODE --

    @pytest.mark.integration
    def test_contract_weak_node_creates_parent(self) -> None:
        """WeakNode amb insert_parent=True insereix el parent automàticament."""
        graph = self._make_graph()
        parent = Node(pk={"id": 1}, main_label="ParentNode")
        child = WeakNode(parent=parent, pk={"sub": 1}, main_label="ChildNode")
        graph.insertNode(parent, replace=True)
        graph.insertNode(child, insert_parent=True)
        self.assertEqual(len(graph.get_node_ids()), 2)  # parent + child
        self.assertEqual(len(graph.get_edges()), 1)  # parent → child
        graph.close()

    @pytest.mark.integration
    def test_contract_weak_node_composite_pk(self) -> None:
        """WeakNode té PK composta fusionada amb el parent."""
        graph = self._make_graph()
        parent = Node(pk={"id": 1}, main_label="ParentNode")
        child = WeakNode(parent=parent, pk={"sub": 1}, main_label="ChildNode")
        graph.insertNode(parent, replace=True)
        graph.insertNode(child, insert_parent=True)
        # La PK del child ha de contenir la del parent
        self.assertIn("id", child._primary_key)
        self.assertIn("sub", child._primary_key)
        graph.close()

    @pytest.mark.integration
    def test_contract_weak_node_propagation_delete(self) -> None:
        """Esborrar parent amb propagation=True esborra el fill WeakNode.

        La propagació es basa en l'aresta parent-child amb
        _propagate=TRUE.  Cal assegurar-se que l'aresta té aquest flag.
        """
        graph = self._make_graph()
        parent = Node(pk={"id": 1}, main_label="ParentNode")
        child = WeakNode(parent=parent, pk={"sub": 1}, main_label="ChildNode")
        graph.insertNode(parent, replace=True)
        graph.insertNode(child, insert_parent=True)
        # Assegurar que l'aresta té _propagate=True
        edges = graph.get_edges()
        if edges:
            src_id, dst_id, rel_type = edges[0]
            attrs = graph.get_edge_attrs(src_id, dst_id, rel_type)
            if attrs is not None:
                attrs["_propagate"] = True
        result = graph.deleteNode(parent, propagation=True, detach=True)
        self.assertTrue(result)
        self.assertEqual(len(graph.get_node_ids()), 0)  # tots dos esborrats
        self.assertEqual(len(graph.get_edges()), 0)
        graph.close()

    @pytest.mark.integration
    def test_contract_weak_node_nested_chain(self) -> None:
        """WeakNode nesting: grandparent → parent weak → child weak."""
        graph = self._make_graph()
        grandparent = Node(pk={"id": 1}, main_label="Document")
        parent_weak = WeakNode(parent=grandparent, pk={"section": 1}, main_label="Section")
        child_weak = WeakNode(parent=parent_weak, pk={"page": 1}, main_label="Page")
        graph.insertNode(grandparent, replace=True)
        graph.insertNode(parent_weak, insert_parent=True)
        graph.insertNode(child_weak, insert_parent=True)
        self.assertEqual(len(graph.get_node_ids()), 3)
        self.assertEqual(len(graph.get_edges()), 2)  # gp→pw, pw→cw
        graph.close()

    @pytest.mark.integration
    def test_contract_weak_node_nested_delete_cascade(self) -> None:
        """ON DELETE CASCADE en cadena de WeakNodes: esborrar grandparent."""
        graph = self._make_graph()
        grandparent = Node(pk={"id": 1}, main_label="Document")
        parent_weak = WeakNode(parent=grandparent, pk={"section": 1}, main_label="Section")
        child_weak = WeakNode(parent=parent_weak, pk={"page": 1}, main_label="Page")
        graph.insertNode(grandparent, replace=True)
        graph.insertNode(parent_weak, insert_parent=True)
        graph.insertNode(child_weak, insert_parent=True)
        graph.deleteNode(grandparent, detach=True)
        # CASCADE esborra arestes però NO nodes veïns
        self.assertEqual(len(graph.get_node_ids()), 2)  # pw, cw queden
        self.assertEqual(len(graph.get_edges()), 1)  # pw→cw es manté
        graph.close()

    # -- BULK IMPORT (create) --

    @pytest.mark.integration
    def test_contract_bulk_import(self) -> None:
        """Bulk import: inserta nodes i relacions en un sol pas."""
        graph = self._make_graph()
        nodes: List[Node] = [
            Node(pk={"id": 1}, main_label="TestNode"),
            Node(pk={"id": 2}, main_label="TestNode"),
        ]
        rel = Relation(nodes[0], nodes[1], "LINKS")
        migration: Tuple[List, List] = (nodes, [rel])
        graph.create(migration)
        self.assertEqual(len(graph.get_node_ids()), 2)
        self.assertEqual(len(graph.get_edges()), 1)
        graph.close()

    # -- CHECK NODE --

    @pytest.mark.integration
    def test_contract_get_node_pks_includes_main_label(self) -> None:
        """get_node_pks retorna el main_label real de cada node, no ''."""
        graph = self._make_graph()
        graph.insertNode(Node(pk={"id": 1}, main_label="Document"), replace=True)
        graph.insertNode(Node(pk={"id": 2}, main_label="Author"), replace=True)
        pks_by_label = {p["main_label"]: p["pk"] for p in graph.get_node_pks()}
        self.assertEqual(pks_by_label, {"Document": {"id": 1}, "Author": {"id": 2}})
        graph.close()

    @pytest.mark.integration
    def test_contract_check_node_exists(self) -> None:
        """checkNode troba un node que existeix."""
        graph = self._make_graph()
        a = Node(pk={"id": 1}, main_label="TestNode")
        graph.insertNode(a, replace=True)
        result = graph.checkNode(a)
        self.assertIsNotNone(result)
        graph.close()

    @pytest.mark.integration
    def test_contract_check_node_missing(self) -> None:
        """checkNode retorna None per a nodes inexistents."""
        graph = self._make_graph()
        a = Node(pk={"id": 999}, main_label="NonExistent")
        result = graph.checkNode(a)
        self.assertIsNone(result)
        graph.close()

    # -- DUPLICATE KEY --

    @pytest.mark.integration
    def test_contract_duplicate_key_raises(self) -> None:
        """Inserir el mateix node dues vegades amb update=False + replace=False
        llança RuntimeError (duplicate key)."""
        graph = self._make_graph()
        a1 = Node(pk={"id": 1}, main_label="TestNode")
        a2 = Node(pk={"id": 1}, main_label="TestNode")
        graph.insertNode(a1, replace=True)
        with self.assertRaises(RuntimeError) as ctx:
            graph.insertNode(a2, update=False, replace=False)
        self.assertIn("Duplicate key", str(ctx.exception))
        graph.close()

    # -- CLOSE --

    @pytest.mark.integration
    def test_contract_close_is_safe(self) -> None:
        """close() no llança excepcions i deixa el store en estat net."""
        graph = self._make_graph()
        a = Node(pk={"id": 1}, main_label="TestNode")
        graph.insertNode(a, replace=True)
        graph.close()
        # Després de close, no hauria de quedar estat inconsistent
        self.assertEqual(len(graph.get_node_ids()), 0)

    # -- EXPLICIT PK=None --

    @pytest.mark.integration
    def test_contract_explicit_pk_none_before_insert(self) -> None:
        """Node amb pk=None explícit: _primary_key = None abans d'insertar."""
        graph = self._make_graph()
        node = Node(pk=None, main_label="AutoIdNode")
        self.assertIsNone(node._primary_key)
        graph.close()

    @pytest.mark.integration
    def test_contract_explicit_pk_none_assigned_after_insert(self) -> None:
        """Node amb pk=None explícit: el backend assigna un ID com a PK."""
        graph = self._make_graph()
        node = Node(pk=None, main_label="AutoIdNode")
        self.assertIsNone(node._primary_key)
        graph.insertNode(node, replace=True)
        # El backend ha assignat un ID com a PK
        self.assertIsNotNone(node._primary_key)
        self.assertIn("id", node._primary_key)
        self.assertEqual(node._primary_key["id"], node.neo4j_id)
        # Ara checkNode el pot trobar
        self.assertEqual(graph.checkNode(node), node.neo4j_id)
        graph.close()

    # -- GET SUBDOCUMENTS --

    @pytest.mark.integration
    def test_contract_get_subdocuments_simple(self) -> None:
        """get_subdocuments: un node fort amb un WeakNode fill."""
        graph = self._make_graph()
        parent = Node(pk={"id": 1}, main_label="Document")
        child = WeakNode(parent=parent, pk={"section": "intro"}, main_label="Section")
        graph.insertNode(parent, replace=True)
        graph.insertNode(child, insert_parent=True)
        result = graph.get_subdocuments(parent)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "Section")
        self.assertEqual(result[0]["pk"]["section"], "intro")
        graph.close()

    @pytest.mark.integration
    def test_contract_get_subdocuments_nested(self) -> None:
        """get_subdocuments: cadena Document → Section → Page."""
        graph = self._make_graph()
        doc = Node(pk={"id": 1}, main_label="Document")
        section = WeakNode(parent=doc, pk={"section": "intro"}, main_label="Section")
        page = WeakNode(parent=section, pk={"page": 1}, main_label="Page")
        graph.insertNode(doc, replace=True)
        graph.insertNode(section, insert_parent=True)
        graph.insertNode(page, insert_parent=True)
        result = graph.get_subdocuments(doc)
        labels = {r["label"] for r in result}
        self.assertIn("Section", labels)
        self.assertIn("Page", labels)
        graph.close()

    @pytest.mark.integration
    def test_contract_get_subdocuments_empty(self) -> None:
        """get_subdocuments: node sense WeakNodes retorna []."""
        graph = self._make_graph()
        node = Node(pk={"id": 1}, main_label="Document")
        graph.insertNode(node, replace=True)
        result = graph.get_subdocuments(node)
        self.assertEqual(result, [])
        graph.close()

    @pytest.mark.integration
    def test_contract_get_subdocuments_no_propagate(self) -> None:
        """get_subdocuments: relacions sense _propagate no es segueixen."""
        graph = self._make_graph()
        parent = Node(pk={"id": 1}, main_label="Document")
        child = Node(pk={"id": 2}, main_label="Section")  # no es WeakNode
        graph.insertNode(parent, replace=True)
        graph.insertNode(child, replace=True)
        graph.insertRelation(Relation(parent, child, "HAS_SECTION"))
        # L'aresta no té _propagate=True → no s'ha de seguir
        result = graph.get_subdocuments(parent)
        self.assertEqual(result, [])
        graph.close()

    @pytest.mark.integration
    def test_contract_get_subdocuments_multiple_children(self) -> None:
        """get_subdocuments: un pare amb múltiples fills."""
        graph = self._make_graph()
        parent = Node(pk={"id": 1}, main_label="Document")
        child1 = WeakNode(parent=parent, pk={"section": "intro"}, main_label="Section")
        child2 = WeakNode(parent=parent, pk={"section": "conclusion"}, main_label="Section")
        graph.insertNode(parent, replace=True)
        graph.insertNode(child1, insert_parent=True)
        graph.insertNode(child2, insert_parent=True)
        result = graph.get_subdocuments(parent)
        self.assertEqual(len(result), 2)
        sections = {r["pk"]["section"] for r in result}
        self.assertEqual(sections, {"intro", "conclusion"})

    # -- GET DEPENDENCY VALUE (be_value_properties resolution) --

    @pytest.mark.integration
    def test_contract_get_dependency_value_follows_single_edge(self) -> None:
        """get_dependency_value segueix l'edge tipat fins al node connectat."""
        graph = self._make_graph()
        person = Node(pk={"id": 1}, main_label="TestNode")
        valor = Node(pk={"name": "oriol"}, main_label="Valor")
        graph.insertNode(person, replace=True)
        graph.insertNode(valor, replace=True)
        graph.insertRelation(Relation(person, valor, "NOM"))
        self.assertEqual(
            graph.get_dependency_value(person.neo4j_id, "NOM"), "oriol"
        )
        graph.close()

    @pytest.mark.integration
    def test_contract_get_dependency_value_missing_edge_returns_none(self) -> None:
        """get_dependency_value retorna None si no hi ha edge d'aquest tipus."""
        graph = self._make_graph()
        person = Node(pk={"id": 1}, main_label="TestNode")
        graph.insertNode(person, replace=True)
        self.assertIsNone(graph.get_dependency_value(person.neo4j_id, "NOM"))
        graph.close()

    @pytest.mark.integration
    def test_contract_get_dependency_value_missing_node_returns_none(self) -> None:
        """get_dependency_value retorna None per a un node inexistent."""
        graph = self._make_graph()
        self.assertIsNone(graph.get_dependency_value(999999, "NOM"))
        graph.close()


class TestNeo4jGraph(unittest.TestCase):
    """Contract tests for Neo4jGraph.

    These tests require a real Neo4j database because many contract
    checks rely on query helpers (get_nodes, get_edges, etc.) that
    would need full Cypher mocking. By default they use the DEV target
    (``NEO4J_DEV_*``).  If no suitable environment vars are set,
    they are skipped with a note.

    To run:
        export NEO4J_DEV_URL=bolt://localhost:7687
        export NEO4J_DEV_USER=neo4j
        export NEO4J_DEV_PASSWORD=secret
        python -m unittest test.test_graph_store_contract.TestNeo4jGraph
    """

    _has_db: bool = False
    _graph: Optional[GraphStore] = None

    @staticmethod
    def _load_config() -> Optional[Dict[str, str]]:
        target = os.environ.get("NEO4J_TARGET", "DEV")

        def _env(key: str) -> Optional[str]:
            targeted = os.environ.get(f"NEO4J_{target.upper()}_{key}")
            if targeted:
                return targeted
            return os.environ.get(f"NEO4J_{key}")

        url = _env("URL")
        user = _env("USER")
        password = _env("PASSWORD")
        database = _env("DATABASE")
        if not url or not user or not password:
            return None
        return {
            "url": url,
            "user": user,
            "password": password,
            "database": database or "neo4j",
        }

    @classmethod
    def setUpClass(cls) -> None:
        config = cls._load_config()
        if config is not None:
            cls._has_db = True
            from cvcdocdb.neo4j_graph import Neo4jGraph
            cls._graph = Neo4jGraph(
                url=config["url"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
            )
            # Clean slate
            try:
                cls._graph._tx = cls._graph._session.begin_transaction()
                cls._graph._tx.run("MATCH (n) DETACH DELETE n")
                cls._graph._tx.commit()
            except Exception:
                pass
            finally:
                cls._graph._tx = None
        else:
            cls._graph = None

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._graph is not None:
            cls._graph.close()

    def _make_graph(self) -> GraphStore:
        if self._has_db:
            return self._graph  # type: ignore[return-value]
        self.skipTest("NEO4J_DEV_* (or compatible NEO4J target/plain vars) not set")
        raise RuntimeError("No Neo4j database available")

    def setUp(self) -> None:
        if self._has_db:
            # Clean slate before each test
            try:
                self._graph._tx = self._graph._session.begin_transaction()
                self._graph._tx.run("MATCH (n) DETACH DELETE n")
                self._graph._tx.commit()
            except Exception:
                pass
            finally:
                self._graph._tx = None
            self._graph._node_pks.clear()
            self._graph._closed = False

    # -- ON DELETE RESTRICT --

    @pytest.mark.slow
    def test_contract_delete_restrict_refuses_with_edges(self) -> None:
        graph = self._make_graph()
        a, b, c = _setup_parent_graph(graph)
        with self.assertRaises(RuntimeError) as ctx:
            graph.deleteNode(a, detach=False)
        self.assertIn("ON DELETE RESTRICT", str(ctx.exception))

    @pytest.mark.slow
    def test_contract_delete_restrict_succeeds_without_edges(self) -> None:
        graph = self._make_graph()
        a = Node(pk={"id": 1}, main_label="TestNode")
        graph.insertNode(a, replace=True)
        result = graph.deleteNode(a, detach=False)
        self.assertTrue(result)

    # -- ON DELETE CASCADE --

    @pytest.mark.slow
    def test_contract_delete_cascade_removes_edges(self) -> None:
        graph = self._make_graph()
        a, b, c = _setup_parent_graph(graph)
        result = graph.deleteNode(a, detach=True)
        self.assertTrue(result)
        self.assertEqual(len(graph.get_node_ids()), 2)
        self.assertEqual(len(graph.get_edges()), 0)

    @pytest.mark.slow
    def test_contract_delete_cascade_leaves_orphans(self) -> None:
        graph = self._make_graph()
        a, b, c = _setup_parent_graph(graph)
        graph.deleteNode(a, detach=True)
        pks = [p["pk"] for p in graph.get_node_pks()]
        self.assertIn({"id": 2}, pks)
        pks = [p["pk"] for p in graph.get_node_pks()]
        self.assertIn({"id": 3}, pks)

    @pytest.mark.slow
    def test_contract_delete_cascade_chain(self) -> None:
        graph = self._make_graph()
        a, b, c = _setup_chain_graph(graph)
        graph.deleteNode(a, detach=True)
        self.assertEqual(len(graph.get_node_ids()), 2)
        self.assertEqual(len(graph.get_edges()), 1)

    # -- ON DELETE SET NULL --

    @pytest.mark.slow
    def test_contract_delete_set_null_keeps_neighbors(self) -> None:
        graph = self._make_graph()
        a, b, c = _setup_parent_graph(graph)
        graph.deleteNode(a, detach=True, on_delete="set_null")
        self.assertEqual(len(graph.get_node_ids()), 2)
        self.assertEqual(len(graph.get_edges()), 0)
        pks = [p["pk"] for p in graph.get_node_pks()]
        self.assertIn({"id": 2}, pks)
        pks = [p["pk"] for p in graph.get_node_pks()]
        self.assertIn({"id": 3}, pks)

    @pytest.mark.slow
    def test_contract_delete_set_null_no_cascade_to_neighbors(self) -> None:
        graph = self._make_graph()
        a, b, c = _setup_chain_graph(graph)
        graph.deleteNode(b, detach=True, on_delete="set_null")
        self.assertEqual(len(graph.get_node_ids()), 2)
        pks = [p["pk"] for p in graph.get_node_pks()]
        self.assertIn({"id": 1}, pks)
        pks = [p["pk"] for p in graph.get_node_pks()]
        self.assertIn({"id": 3}, pks)

    # -- ON UPDATE CASCADE --

    @pytest.mark.slow
    def test_contract_update_preserves_edges(self) -> None:
        graph = self._make_graph()
        a, b, c = _setup_parent_graph(graph)
        a_updated = Node(pk={"id": 1}, main_label="TestNode", updated="yes")
        graph.insertNode(a_updated, update=True)
        self.assertEqual(len(graph.get_node_ids()), 3)
        self.assertEqual(len(graph.get_edges()), 2)

    # -- REPLACE --

    @pytest.mark.slow
    def test_contract_replace_removes_edges(self) -> None:
        graph = self._make_graph()
        a, b, c = _setup_parent_graph(graph)
        a_new = Node(pk={"id": 1}, main_label="TestNode", replaced="yes")
        graph.insertNode(a_new, replace=True)
        self.assertEqual(len(graph.get_node_ids()), 3)
        self.assertEqual(len(graph.get_edges()), 0)

    # -- FK VIOLATION --

    @pytest.mark.slow
    def test_contract_fk_violation_src_missing(self) -> None:
        graph = self._make_graph()
        src = Node(pk={"id": 999}, main_label="MissingNode")
        dst = Node(pk={"id": 1}, main_label="TestNode")
        graph.insertNode(dst, replace=True)
        rel = Relation(src, dst, "CONNECTS")
        with self.assertRaises(RuntimeError) as ctx:
            graph.insertRelation(rel)
        self.assertIn("FK violation", str(ctx.exception))
        self.assertIn("src", str(ctx.exception))

    @pytest.mark.slow
    def test_contract_fk_violation_dst_missing(self) -> None:
        graph = self._make_graph()
        src = Node(pk={"id": 1}, main_label="TestNode")
        dst = Node(pk={"id": 999}, main_label="MissingNode")
        graph.insertNode(src, replace=True)
        rel = Relation(src, dst, "CONNECTS")
        with self.assertRaises(RuntimeError) as ctx:
            graph.insertRelation(rel)
        self.assertIn("FK violation", str(ctx.exception))
        self.assertIn("dst", str(ctx.exception))

    # -- WEAK NODE --

    @pytest.mark.slow
    def test_contract_weak_node_creates_parent(self) -> None:
        graph = self._make_graph()
        parent = Node(pk={"id": 1}, main_label="ParentNode")
        child = WeakNode(parent=parent, pk={"sub": 1}, main_label="ChildNode")
        graph.insertNode(parent, replace=True)
        graph.insertNode(child, insert_parent=True)
        self.assertEqual(len(graph.get_node_ids()), 2)
        self.assertEqual(len(graph.get_edges()), 1)

    @pytest.mark.slow
    def test_contract_weak_node_composite_pk(self) -> None:
        graph = self._make_graph()
        parent = Node(pk={"id": 1}, main_label="ParentNode")
        child = WeakNode(parent=parent, pk={"sub": 1}, main_label="ChildNode")
        graph.insertNode(parent, replace=True)
        graph.insertNode(child, insert_parent=True)
        self.assertIn("id", child._primary_key)
        self.assertIn("sub", child._primary_key)

    @pytest.mark.slow
    def test_contract_weak_node_propagation_delete(self) -> None:
        graph = self._make_graph()
        parent = Node(pk={"id": 1}, main_label="ParentNode")
        child = WeakNode(parent=parent, pk={"sub": 1}, main_label="ChildNode")
        graph.insertNode(parent, replace=True)
        graph.insertNode(child, insert_parent=True)
        edges = graph.get_edges()
        if edges:
            src_id, dst_id, rel_type = edges[0]
            attrs = graph.get_edge_attrs(src_id, dst_id, rel_type)
            if attrs is not None:
                attrs["_propagate"] = True
        result = graph.deleteNode(parent, propagation=True, detach=True)
        self.assertTrue(result)
        self.assertEqual(len(graph.get_node_ids()), 0)
        self.assertEqual(len(graph.get_edges()), 0)

    @pytest.mark.slow
    def test_contract_weak_node_nested_chain(self) -> None:
        graph = self._make_graph()
        grandparent = Node(pk={"id": 1}, main_label="Document")
        parent_weak = WeakNode(parent=grandparent, pk={"section": 1}, main_label="Section")
        child_weak = WeakNode(parent=parent_weak, pk={"page": 1}, main_label="Page")
        graph.insertNode(grandparent, replace=True)
        graph.insertNode(parent_weak, insert_parent=True)
        graph.insertNode(child_weak, insert_parent=True)
        self.assertEqual(len(graph.get_node_ids()), 3)
        self.assertEqual(len(graph.get_edges()), 2)

    @pytest.mark.slow
    def test_contract_weak_node_nested_delete_cascade(self) -> None:
        graph = self._make_graph()
        grandparent = Node(pk={"id": 1}, main_label="Document")
        parent_weak = WeakNode(parent=grandparent, pk={"section": 1}, main_label="Section")
        child_weak = WeakNode(parent=parent_weak, pk={"page": 1}, main_label="Page")
        graph.insertNode(grandparent, replace=True)
        graph.insertNode(parent_weak, insert_parent=True)
        graph.insertNode(child_weak, insert_parent=True)
        graph.deleteNode(grandparent, detach=True)
        self.assertEqual(len(graph.get_node_ids()), 2)
        self.assertEqual(len(graph.get_edges()), 1)

    # -- BULK IMPORT --

    @pytest.mark.slow
    def test_contract_bulk_import(self) -> None:
        graph = self._make_graph()
        nodes: List[Node] = [
            Node(pk={"id": 1}, main_label="TestNode"),
            Node(pk={"id": 2}, main_label="TestNode"),
        ]
        rel = Relation(nodes[0], nodes[1], "LINKS")
        migration: Tuple[List, List] = (nodes, [rel])
        graph.create(migration)
        self.assertEqual(len(graph.get_node_ids()), 2)
        self.assertEqual(len(graph.get_edges()), 1)

    # -- GET NODE PKS --

    @pytest.mark.slow
    def test_contract_get_node_pks_includes_main_label(self) -> None:
        """get_node_pks retorna el main_label real de cada node, no ''."""
        graph = self._make_graph()
        graph.insertNode(Node(pk={"id": 1}, main_label="Document"), replace=True)
        graph.insertNode(Node(pk={"id": 2}, main_label="Author"), replace=True)
        pks_by_label = {p["main_label"]: p["pk"] for p in graph.get_node_pks()}
        self.assertEqual(pks_by_label, {"Document": {"id": 1}, "Author": {"id": 2}})

    # -- CHECK NODE --

    @pytest.mark.slow
    def test_contract_check_node_exists(self) -> None:
        graph = self._make_graph()
        a = Node(pk={"id": 1}, main_label="TestNode")
        graph.insertNode(a, replace=True)
        result = graph.checkNode(a)
        self.assertIsNotNone(result)

    @pytest.mark.slow
    def test_contract_check_node_missing(self) -> None:
        graph = self._make_graph()
        a = Node(pk={"id": 999}, main_label="NonExistent")
        result = graph.checkNode(a)
        self.assertIsNone(result)

    # -- DUPLICATE KEY --

    @pytest.mark.slow
    def test_contract_duplicate_key_raises(self) -> None:
        graph = self._make_graph()
        a1 = Node(pk={"id": 1}, main_label="TestNode")
        a2 = Node(pk={"id": 1}, main_label="TestNode")
        graph.insertNode(a1, replace=True)
        with self.assertRaises(RuntimeError) as ctx:
            graph.insertNode(a2, update=False, replace=False)
        self.assertIn("Duplicate key", str(ctx.exception))

    # -- CLOSE --

    @pytest.mark.slow
    def test_contract_close_is_safe(self) -> None:
        """close() no llança excepcions i deixa el store en estat net.

        Uses its own dedicated connection rather than the class-shared
        ``cls._graph`` — every other test in this class reuses that shared
        instance, and calling close() on it would tear down the underlying
        driver/session for the rest of the suite.
        """
        if not self._has_db:
            self.skipTest("NEO4J_DEV_* (or compatible NEO4J target/plain vars) not set")
        from cvcdocdb.neo4j_graph import Neo4jGraph

        config = self._load_config()
        graph = Neo4jGraph(
            url=config["url"],
            user=config["user"],
            password=config["password"],
            database=config["database"],
        )
        try:
            a = Node(pk={"id": 1}, main_label="TestNode")
            graph.insertNode(a, replace=True)
        finally:
            graph.close()
        self.assertEqual(len(graph.get_node_ids()), 0)

    # -- EXPLICIT PK=None --

    @pytest.mark.slow
    def test_contract_explicit_pk_none_before_insert(self) -> None:
        """Node amb pk=None explícit: _primary_key = None abans d'insertar."""
        graph = self._make_graph()
        node = Node(pk=None, main_label="AutoIdNode")
        self.assertIsNone(node._primary_key)

    @pytest.mark.slow
    def test_contract_explicit_pk_none_assigned_after_insert(self) -> None:
        """Node amb pk=None explícit: el backend assigna un ID com a PK."""
        graph = self._make_graph()
        node = Node(pk=None, main_label="AutoIdNode")
        self.assertIsNone(node._primary_key)
        graph.insertNode(node, replace=True)
        # El backend ha assignat un ID com a PK
        self.assertIsNotNone(node._primary_key)
        self.assertIn("id", node._primary_key)
        self.assertEqual(node._primary_key["id"], node.neo4j_id)
        # Ara checkNode el pot trobar
        self.assertEqual(graph.checkNode(node), node.neo4j_id)

    @pytest.mark.slow
    def test_contract_weak_node_no_pk(self) -> None:
        """WeakNode sense PK: el backend assigna l'ID com a PK."""
        graph = self._make_graph()
        parent = Node(pk={"id": 42}, main_label="Document")
        child = WeakNode(parent=parent, pk=None, main_label="Page")
        parent_id = graph.insertNode(parent, replace=True)
        child_id = graph.insertNode(child, insert_parent=True)
        self.assertIsNotNone(parent_id)
        self.assertIsNotNone(child_id)
        self.assertNotEqual(parent_id, child_id)
        self.assertEqual(len(graph.get_node_ids()), 2)

    # -- GET SUBDOCUMENTS --

    @pytest.mark.slow
    def test_contract_get_subdocuments_simple(self) -> None:
        """get_subdocuments: un node fort amb un WeakNode fill."""
        graph = self._make_graph()
        parent = Node(pk={"id": 1}, main_label="Document")
        child = WeakNode(parent=parent, pk={"section": "intro"}, main_label="Section")
        graph.insertNode(parent, replace=True)
        graph.insertNode(child, insert_parent=True)
        result = graph.get_subdocuments(parent)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "Section")
        self.assertEqual(result[0]["pk"]["section"], "intro")
        graph.close()

    @pytest.mark.slow
    def test_contract_get_subdocuments_nested(self) -> None:
        """get_subdocuments: cadena Document → Section → Page."""
        graph = self._make_graph()
        doc = Node(pk={"id": 1}, main_label="Document")
        section = WeakNode(parent=doc, pk={"section": "intro"}, main_label="Section")
        page = WeakNode(parent=section, pk={"page": 1}, main_label="Page")
        graph.insertNode(doc, replace=True)
        graph.insertNode(section, insert_parent=True)
        graph.insertNode(page, insert_parent=True)
        result = graph.get_subdocuments(doc)
        labels = {r["label"] for r in result}
        self.assertIn("Section", labels)
        self.assertIn("Page", labels)
        graph.close()

    @pytest.mark.slow
    def test_contract_get_subdocuments_empty(self) -> None:
        """get_subdocuments: node sense WeakNodes retorna []."""
        graph = self._make_graph()
        node = Node(pk={"id": 1}, main_label="Document")
        graph.insertNode(node, replace=True)
        result = graph.get_subdocuments(node)
        self.assertEqual(result, [])
        graph.close()

    @pytest.mark.slow
    def test_contract_get_subdocuments_no_propagate(self) -> None:
        """get_subdocuments: relacions sense _propagate no es segueixen."""
        graph = self._make_graph()
        parent = Node(pk={"id": 1}, main_label="Document")
        child = Node(pk={"id": 2}, main_label="Section")  # no es WeakNode
        graph.insertNode(parent, replace=True)
        graph.insertNode(child, replace=True)
        graph.insertRelation(Relation(parent, child, "HAS_SECTION"))
        # L'aresta no té _propagate=True → no s'ha de seguir
        result = graph.get_subdocuments(parent)
        self.assertEqual(result, [])
        graph.close()

    @pytest.mark.slow
    def test_contract_get_subdocuments_multiple_children(self) -> None:
        """get_subdocuments: un pare amb múltiples fills."""
        graph = self._make_graph()
        parent = Node(pk={"id": 1}, main_label="Document")
        child1 = WeakNode(parent=parent, pk={"section": "intro"}, main_label="Section")
        child2 = WeakNode(parent=parent, pk={"section": "conclusion"}, main_label="Section")
        graph.insertNode(parent, replace=True)
        graph.insertNode(child1, insert_parent=True)
        graph.insertNode(child2, insert_parent=True)
        result = graph.get_subdocuments(parent)
        self.assertEqual(len(result), 2)
        sections = {r["pk"]["section"] for r in result}
        self.assertEqual(sections, {"intro", "conclusion"})
        graph.close()

    # -- GET DEPENDENCY VALUE (be_value_properties resolution) --

    @pytest.mark.slow
    def test_contract_get_dependency_value_follows_single_edge(self) -> None:
        """get_dependency_value segueix l'edge tipat fins al node connectat."""
        graph = self._make_graph()
        person = Node(pk={"id": 1}, main_label="TestNode")
        valor = Node(pk={"name": "oriol"}, main_label="Valor")
        graph.insertNode(person, replace=True)
        graph.insertNode(valor, replace=True)
        graph.insertRelation(Relation(person, valor, "NOM"))
        self.assertEqual(
            graph.get_dependency_value(person.neo4j_id, "NOM"), "oriol"
        )

    @pytest.mark.slow
    def test_contract_get_dependency_value_missing_edge_returns_none(self) -> None:
        """get_dependency_value retorna None si no hi ha edge d'aquest tipus."""
        graph = self._make_graph()
        person = Node(pk={"id": 1}, main_label="TestNode")
        graph.insertNode(person, replace=True)
        self.assertIsNone(graph.get_dependency_value(person.neo4j_id, "NOM"))

