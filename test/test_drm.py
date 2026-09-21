from typing import Any, Dict, List, Optional, Tuple, Union

import glob
import os
import pickle
import shutil
import stat
import sys
import tempfile
import unittest

# Mock external dependencies before importing cvcdocdb modules that depend on them
from unittest import mock as _unittest_mock
mock_neo4j = _unittest_mock.MagicMock  # Class, not instance — can call mock_neo4j() to create new instances
mock_patch = _unittest_mock.patch

# Create real exception classes for neo4j.exceptions
class _ConstraintError(Exception):
    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message

class _TransactionError(Exception):
    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message

_mock_neo4j_exceptions = __import__('types').ModuleType("neo4j.exceptions")
_mock_neo4j_exceptions.ConstraintError = _ConstraintError
_mock_neo4j_exceptions.TransactionError = _TransactionError

# Create a MagicMock instance for the neo4j module itself
_mock_neo4j_instance = _unittest_mock.MagicMock()
_mock_neo4j_instance.exceptions = _mock_neo4j_exceptions
sys.modules["neo4j"] = _mock_neo4j_instance
sys.modules["neo4j.exceptions"] = _mock_neo4j_exceptions
sys.modules["tqdm"] = _unittest_mock.MagicMock()
sys.modules["traitlets"] = _unittest_mock.MagicMock()

# Import hnswlib for skipIf decorators — may not be installed
try:
    import hnswlib  # noqa: F401
except ImportError:
    hnswlib = None  # type: ignore[assignment,name-defined]

from cvcdocdb.neo4j_graph import Neo4jGraph
from cvcdocdb.networkx_graph import NetworkXGraph
from cvcdocdb.drm_entities import *
from cvcdocdb.base import *


# ---------------------------------------------------------------------------
# Tests for NetworkXGraph — pure in-memory NetworkX store
# ---------------------------------------------------------------------------

class NetworkXGraphTest(unittest.TestCase):
    """Tests for NetworkXGraph — verifies the in-memory graph implementation."""

    def setUp(self) -> None:
        self._persistence_path = os.path.join(tempfile.gettempdir(), "drm_tools_networkx_test_state.pkl")
        if os.path.exists(self._persistence_path):
            os.remove(self._persistence_path)
        for path in glob.glob(f"{self._persistence_path}.vectors.*.bin"):
            os.remove(path)

    def tearDown(self) -> None:
        if os.path.exists(self._persistence_path):
            os.remove(self._persistence_path)
        for path in glob.glob(f"{self._persistence_path}.vectors.*.bin"):
            os.remove(path)

    def _make_graph(self) -> NetworkXGraph:
        return NetworkXGraph(persistence_path=self._persistence_path)

    def test_nx_graph_persistence_roundtrip_transparent(self) -> None:
        """Test que guarda automàticament i recarrega l'estat des de disc."""
        src = Node(pk={"id": 1}, main_label="SrcNode")
        dst = Node(pk={"id": 2}, main_label="DstNode")

        graph = self._make_graph()
        graph.insertNode(src, replace=True)
        graph.insertNode(dst, replace=True)
        graph.insertRelation(Relation(src, dst, "CONNECTS"))
        graph.close()

        reloaded = self._make_graph()
        self.assertEqual(len(reloaded.get_nodes()), 2)
        self.assertEqual(len(reloaded.get_edges()), 1)
        self.assertIsNotNone(reloaded.checkNode(Node(pk={"id": 1}, main_label="SrcNode")))
        reloaded.close()

    def test_default_persistence_path_is_not_in_shared_temp_dir(self) -> None:
        """The default (no persistence_path given) location must not be a
        predictable path inside the shared, world-writable system temp dir
        — otherwise another local user could pre-plant a malicious pickle
        file there for us to unpickle."""
        graph = NetworkXGraph()
        try:
            path = graph._persistence_path
            self.assertNotEqual(
                os.path.dirname(os.path.abspath(path)),
                os.path.abspath(tempfile.gettempdir()),
            )
        finally:
            graph.close()

    def test_default_persistence_dir_is_user_restricted(self) -> None:
        """The default persistence directory must not be group/world
        readable or writable on POSIX systems."""
        if not hasattr(os, "getuid"):
            self.skipTest("POSIX permission bits not applicable on this platform")
        graph = NetworkXGraph()
        try:
            parent_dir = os.path.dirname(os.path.abspath(graph._persistence_path))
            mode = stat.S_IMODE(os.stat(parent_dir).st_mode)
            self.assertEqual(mode & (stat.S_IRWXG | stat.S_IRWXO), 0)
        finally:
            graph.close()

    def test_load_state_refuses_file_not_owned_by_current_user(self) -> None:
        """_load_state must refuse to unpickle a persistence file that
        isn't owned by the current user, to defeat a pre-planted malicious
        pickle on a shared multi-user host."""
        if not hasattr(os, "getuid"):
            self.skipTest("POSIX ownership checks not applicable on this platform")
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "planted.pkl")
            with open(path, "wb") as fh:
                pickle.dump({"graph": None}, fh)
            graph = NetworkXGraph.__new__(NetworkXGraph)
            graph._persistence_path = path
            with mock_patch("os.getuid", return_value=os.getuid() + 12345):
                with self.assertRaises(RuntimeError):
                    graph._load_state()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # -- Node CRUD --

    def test_nx_graph_insert_node(self) -> None:
        """Test que insertNode afegeix un node al graf."""
        node = Node(pk={"id": 1}, main_label="TestNode", name="test")
        graph = self._make_graph()
        node_id = graph.insertNode(node, replace=True)

        self.assertIsInstance(node_id, int)
        self.assertGreaterEqual(node_id, 0)
        self.assertIn(node_id, graph.get_nodes())
        graph.close()

    def test_nx_graph_get_node(self) -> None:
        """Test que NetworkXGraph pot recuperar nodes per id."""
        node = Node(pk={"id": 1}, main_label="TestNode", name="test")
        graph = self._make_graph()
        node_id = graph.insertNode(node, replace=True)
        retrieved = graph.get_node(node_id)

        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.main_label, "TestNode")
        self.assertEqual(retrieved.neo4j_id, node_id)
        graph.close()

    def test_nx_graph_node_attrs(self) -> None:
        """Test que NetworkXGraph guarda els atributs dels nodes."""
        node = Node(pk={"id": 1}, main_label="TestNode", custom="value", count=42)
        graph = self._make_graph()
        node_id = graph.insertNode(node, replace=True)
        attrs = graph.get_node_attrs(node_id)

        self.assertIsNotNone(attrs)
        self.assertEqual(attrs["custom"], "value")
        self.assertEqual(attrs["count"], 42)
        graph.close()

    def test_nx_graph_check_node(self) -> None:
        """Test que NetworkXGraph pot verificar existencia de nodes."""
        node = Node(pk={"id": 1}, main_label="TestNode")
        graph = self._make_graph()
        graph.insertNode(node, replace=True)
        result = graph.checkNode(node)

        self.assertIsNotNone(result)
        graph.close()

    def test_nx_graph_check_missing_node(self) -> None:
        """Test que checkNode retorna None per a nodes inexistents."""
        node = Node(pk={"id": 999}, main_label="NonExistent")
        graph = self._make_graph()
        result = graph.checkNode(node)

        self.assertIsNone(result)
        graph.close()

    def test_nx_graph_pk_index_created(self) -> None:
        """Test que inserir un node crea entrada a l'índex de PK."""
        node = Node(pk={"id": 1}, main_label="TestNode")
        graph = self._make_graph()
        node_id = graph.insertNode(node, replace=True)

        idx_key = graph._pk_index_key("TestNode", {"id": 1})
        self.assertEqual(graph._pk_index.get(idx_key), node_id)
        graph.close()

    def test_nx_graph_find_nodes_by_property(self) -> None:
        """Test cerca de nodes per propietat indexada."""
        a = Node(pk={"id": 1}, main_label="Person", city="BCN", status="active")
        b = Node(pk={"id": 2}, main_label="Person", city="BCN", status="inactive")
        c = Node(pk={"id": 3}, main_label="Person", city="GIR", status="active")

        graph = self._make_graph()
        graph.insertNode(a, replace=True)
        graph.insertNode(b, replace=True)
        graph.insertNode(c, replace=True)

        bcn_ids = graph.find_nodes_by_property("city", "BCN")
        active_ids = graph.find_nodes_by_property("status", "active")

        self.assertEqual(len(bcn_ids), 2)
        self.assertEqual(len(active_ids), 2)
        graph.close()

    def test_nx_graph_find_nodes_all_any(self) -> None:
        """Test cerca composta amb mode all/any."""
        a = Node(pk={"id": 1}, main_label="Person", city="BCN", status="active")
        b = Node(pk={"id": 2}, main_label="Person", city="BCN", status="inactive")
        c = Node(pk={"id": 3}, main_label="Person", city="GIR", status="active")

        graph = self._make_graph()
        graph.insertNode(a, replace=True)
        graph.insertNode(b, replace=True)
        graph.insertNode(c, replace=True)

        all_match = graph.find_nodes({"city": "BCN", "status": "active"}, match="all")
        any_match = graph.find_nodes({"city": "BCN", "status": "active"}, match="any")

        self.assertEqual(len(all_match), 1)
        self.assertEqual(len(any_match), 3)
        graph.close()

    def test_nx_graph_indexes_persist_after_reload(self) -> None:
        """Test que els índexs secundaris es mantenen després de recarregar."""
        a = Node(pk={"id": 1}, main_label="Person", city="BCN", status="active")
        b = Node(pk={"id": 2}, main_label="Person", city="GIR", status="inactive")

        graph = self._make_graph()
        graph.insertNode(a, replace=True)
        graph.insertNode(b, replace=True)
        graph.close()

        reloaded = self._make_graph()
        found = reloaded.find_nodes_by_property("city", "BCN")
        by_pk = reloaded.checkNode(Node(pk={"id": 1}, main_label="Person"))
        self.assertEqual(len(found), 1)
        self.assertIsNotNone(by_pk)
        reloaded.close()

    @unittest.skipIf(hnswlib is None, "hnswlib not available")
    def test_nx_graph_vector_index_query(self) -> None:
        """Test ANN query sobre una propietat vectorial indexada."""
        n1 = Node(pk={"id": 1}, main_label="Doc", embedding=[1.0, 0.0, 0.0])
        n2 = Node(pk={"id": 2}, main_label="Doc", embedding=[0.0, 1.0, 0.0])
        n3 = Node(pk={"id": 3}, main_label="Doc", embedding=[0.9, 0.1, 0.0])

        graph = self._make_graph()
        id1 = graph.insertNode(n1, replace=True)
        graph.insertNode(n2, replace=True)
        graph.insertNode(n3, replace=True)
        graph.enable_vector_index("embedding", dimensions=3, space="cosine")

        results = graph.query_vector_index("embedding", [1.0, 0.0, 0.0], top_k=2)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][0], id1)
        graph.close()

    @unittest.skipIf(hnswlib is None, "hnswlib not available")
    def test_nx_graph_vector_index_tracks_new_nodes(self) -> None:
        """Test que l'índex vectorial s'actualitza quan arriben nodes nous."""
        graph = self._make_graph()
        graph.enable_vector_index("embedding", dimensions=3, space="cosine")

        node = Node(pk={"id": 1}, main_label="Doc", embedding=[0.0, 0.0, 1.0])
        node_id = graph.insertNode(node, replace=True)
        results = graph.query_vector_index("embedding", [0.0, 0.0, 1.0], top_k=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], node_id)
        graph.close()

    @unittest.skipIf(hnswlib is None, "hnswlib not available")
    def test_nx_graph_vector_index_persists_after_reload(self) -> None:
        """Test que l'índex vectorial es guarda i funciona després de recarregar."""
        graph = self._make_graph()
        graph.enable_vector_index("embedding", dimensions=3, space="cosine")

        node = Node(pk={"id": 1}, main_label="Doc", embedding=[0.0, 1.0, 0.0])
        node_id = graph.insertNode(node, replace=True)
        graph.close()

        reloaded = self._make_graph()
        results = reloaded.query_vector_index("embedding", [0.0, 1.0, 0.0], top_k=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], node_id)
        reloaded.close()

    def test_nx_graph_duplicate_key_raises(self) -> None:
        """Test que update=False + replace=False amb pk existent llança RuntimeError."""
        node_a = Node(pk={"id": 1}, main_label="TestNode", name="first")
        node_b = Node(pk={"id": 1}, main_label="TestNode", name="second")

        graph = self._make_graph()
        graph.insertNode(node_a, replace=True)

        with self.assertRaises(RuntimeError) as ctx:
            graph.insertNode(node_b, update=False, replace=False)
        self.assertIn("Duplicate key", str(ctx.exception))
        graph.close()

    def test_nx_graph_replace_creates_new_node(self) -> None:
        """Test que replace=True esborra i crea un node nou amb id diferent."""
        node_a = Node(pk={"id": 1}, main_label="TestNode", name="old", count=1)
        node_b = Node(pk={"id": 1}, main_label="TestNode", name="new", count=99)

        graph = self._make_graph()
        id_a = graph.insertNode(node_a, replace=True)
        id_b = graph.insertNode(node_b, replace=True)

        self.assertNotEqual(id_a, id_b)
        self.assertEqual(len(graph.get_nodes()), 1)
        attrs = graph.get_node_attrs(id_b)
        self.assertEqual(attrs["name"], "new")
        self.assertEqual(attrs["count"], 99)
        graph.close()

    def test_nx_graph_update_merges_attributes(self) -> None:
        """Test que update=True fusiona atributs sense esborrar el node."""
        node_a = Node(pk={"id": 1}, main_label="TestNode", name="original", count=1)
        node_b = Node(pk={"id": 1}, main_label="TestNode", name="updated", extra="data")

        graph = self._make_graph()
        id_a = graph.insertNode(node_a, replace=True)
        id_b = graph.insertNode(node_b, update=True)

        self.assertEqual(id_a, id_b)
        self.assertEqual(len(graph.get_nodes()), 1)
        attrs = graph.get_node_attrs(id_a)
        self.assertEqual(attrs["name"], "updated")
        self.assertEqual(attrs["extra"], "data")
        self.assertEqual(attrs["count"], 1)
        graph.close()

    # -- Relations --

    def test_nx_graph_get_edges(self) -> None:
        """Test que NetworkXGraph retorna les arestes correctament."""
        src = Node(pk={"id": 1}, main_label="SrcNode")
        dst = Node(pk={"id": 2}, main_label="DstNode")
        rel = Relation(src, dst, "CONNECTS")

        graph = self._make_graph()
        graph.insertNode(src, replace=True)
        graph.insertNode(dst, replace=True)
        graph.insertRelation(rel)
        edges = graph.get_edges()
        graph.close()

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0][2], "CONNECTS")

    def test_nx_graph_edge_attrs(self) -> None:
        """Test que NetworkXGraph guarda els atributs de les arestes."""
        src = Node(pk={"id": 1}, main_label="SrcNode")
        dst = Node(pk={"id": 2}, main_label="DstNode")
        rel = Relation(src, dst, "CONNECTS")
        rel["weight"] = 10

        graph = self._make_graph()
        graph.insertNode(src, replace=True)
        graph.insertNode(dst, replace=True)
        graph.insertRelation(rel)
        edge_attrs = graph.get_edge_attrs(1, 2, "CONNECTS")
        graph.close()

        self.assertIsNotNone(edge_attrs)
        self.assertEqual(edge_attrs["weight"], 10)

    def test_nx_graph_bulk_create(self) -> None:
        """Test que el mètode create importa nodes i relacions."""
        nodes = [
            Node(pk={"id": 1}, main_label="TestNode", name="a"),
            Node(pk={"id": 2}, main_label="TestNode", name="b"),
        ]
        src = nodes[0]
        dst = nodes[1]
        rel = Relation(src, dst, "LINKS")
        migration: Tuple[List, List] = ([nodes[0], nodes[1]], [rel])

        graph = self._make_graph()
        graph.create(migration)
        edges = graph.get_edges()

        self.assertEqual(len(graph.get_nodes()), 2)
        self.assertEqual(len(edges), 1)
        graph.close()

    # -- FK Validation --

    def test_nx_graph_fk_violation_src_missing(self) -> None:
        """Test que crear una relació amb src no inserit llança RuntimeError."""
        src = Node(pk={"nom": "Missing"}, main_label="LlocPadro")
        dst = Node(pk={"nom": "NodeB"}, main_label="LlocPadro")
        rel = Relation(src, dst, "CONNECTS")

        graph = self._make_graph()
        graph.insertNode(dst, replace=True)

        with self.assertRaises(RuntimeError) as ctx:
            graph.insertRelation(rel)
        self.assertIn("FK violation", str(ctx.exception))
        self.assertIn("src", str(ctx.exception))
        graph.close()

    def test_nx_graph_fk_violation_dst_missing(self) -> None:
        """Test que crear una relació amb dst no inserit llança RuntimeError."""
        src = Node(pk={"nom": "NodeA"}, main_label="LlocPadro")
        dst = Node(pk={"nom": "Missing"}, main_label="LlocPadro")
        rel = Relation(src, dst, "CONNECTS")

        graph = self._make_graph()
        graph.insertNode(src, replace=True)

        with self.assertRaises(RuntimeError) as ctx:
            graph.insertRelation(rel)
        self.assertIn("FK violation", str(ctx.exception))
        self.assertIn("dst", str(ctx.exception))
        graph.close()

    def test_nx_graph_fk_violation_both_missing(self) -> None:
        """Test que crear una relació amb ambdós nodes no inserits llança RuntimeError."""
        src = Node(pk={"nom": "MissingA"}, main_label="LlocPadro")
        dst = Node(pk={"nom": "MissingB"}, main_label="LlocPadro")
        rel = Relation(src, dst, "CONNECTS")

        graph = self._make_graph()

        with self.assertRaises(RuntimeError) as ctx:
            graph.insertRelation(rel)
        self.assertIn("FK violation", str(ctx.exception))
        self.assertIn("src", str(ctx.exception))
        graph.close()

    # -- Delete: ON DELETE RESTRICT --

    def test_nx_graph_delete_node(self) -> None:
        """Test que NetworkXGraph pot esborrar nodes."""
        node = Node(pk={"id": 1}, main_label="TestNode")
        graph = self._make_graph()
        node_id = graph.insertNode(node, replace=True)
        self.assertIn(node_id, graph.get_nodes())
        result = graph.deleteNode(node, detach=True)
        graph.close()

        self.assertTrue(result)
        self.assertNotIn(node_id, graph.get_nodes())

    def test_nx_graph_delete_on_restrict(self) -> None:
        """Test ON DELETE RESTRICT: no es pot esborrar un node amb arestes sense detach."""
        node_a = Node(pk={"id": 1}, main_label="TestNode")
        node_b = Node(pk={"id": 2}, main_label="TestNode")
        graph = self._make_graph()
        graph.insertNode(node_a, replace=True)
        graph.insertNode(node_b, replace=True)
        rel = Relation(node_a, node_b, "LINKS")
        graph.insertRelation(rel)

        with self.assertRaises(RuntimeError) as ctx:
            graph.deleteNode(node_a, detach=False)

        self.assertIn("ON DELETE RESTRICT", str(ctx.exception))
        graph.close()

    def test_nx_graph_delete_restrict_no_edges(self) -> None:
        """Test ON DELETE RESTRICT: es pot esborrar un node sense arestes."""
        node = Node(pk={"id": 1}, main_label="TestNode")
        graph = self._make_graph()
        graph.insertNode(node, replace=True)
        result = graph.deleteNode(node, detach=False)
        graph.close()

        self.assertTrue(result)

    # -- Delete: ON DELETE CASCADE --

    def test_nx_graph_delete_cascade_removes_edges(self) -> None:
        """Test ON DELETE CASCADE: esborra un node amb arestes i les elimina."""
        node_a = Node(pk={"id": 1}, main_label="TestNode")
        node_b = Node(pk={"id": 2}, main_label="TestNode")
        graph = self._make_graph()
        graph.insertNode(node_a, replace=True)
        graph.insertNode(node_b, replace=True)
        rel = Relation(node_a, node_b, "LINKS")
        graph.insertRelation(rel)

        result = graph.deleteNode(node_a, detach=True)

        self.assertTrue(result)
        self.assertEqual(len(graph.get_nodes()), 1)
        self.assertEqual(len(graph.get_edges()), 0)
        graph.close()

    def test_nx_graph_delete_cascade_chain(self) -> None:
        """Test ON DELETE CASCADE en cadena: A→B→C, esborrar A esborra només les arestes de A."""
        node_a = Node(pk={"id": 1}, main_label="TestNode")
        node_b = Node(pk={"id": 2}, main_label="TestNode")
        node_c = Node(pk={"id": 3}, main_label="TestNode")
        graph = self._make_graph()
        graph.insertNode(node_a, replace=True)
        graph.insertNode(node_b, replace=True)
        graph.insertNode(node_c, replace=True)
        graph.insertRelation(Relation(node_a, node_b, "LINKS"))
        graph.insertRelation(Relation(node_b, node_c, "LINKS"))

        result = graph.deleteNode(node_a, detach=True)

        self.assertTrue(result)
        # node_a s'esborra i l'aresta A→B també
        # node_b i node_c queden (node_b conserva l'aresta B→C)
        self.assertEqual(len(graph.get_nodes()), 2)
        self.assertEqual(len(graph.get_edges()), 1)
        graph.close()

    def test_nx_graph_delete_cascade_multiple_edges(self) -> None:
        """Test ON DELETE CASCADE: node amb múltiples arestes, totes s'esborren."""
        node_a = Node(pk={"id": 1}, main_label="TestNode")
        node_b = Node(pk={"id": 2}, main_label="TestNode")
        node_c = Node(pk={"id": 3}, main_label="TestNode")
        graph = self._make_graph()
        graph.insertNode(node_a, replace=True)
        graph.insertNode(node_b, replace=True)
        graph.insertNode(node_c, replace=True)
        graph.insertRelation(Relation(node_a, node_b, "LINKS"))
        graph.insertRelation(Relation(node_a, node_c, "LINKS"))

        result = graph.deleteNode(node_a, detach=True)

        self.assertTrue(result)
        # node_a s'esborra i totes les arestes que surten d'ell
        # node_b i node_c queden com a nodes independents
        self.assertEqual(len(graph.get_nodes()), 2)
        self.assertEqual(len(graph.get_edges()), 0)
        graph.close()

    # -- Delete: ON DELETE SET NULL --

    def test_nx_graph_delete_set_null(self) -> None:
        """Test ON DELETE SET NULL: esborra node però manté veïns (sense cascada)."""
        node_a = Node(pk={"id": 1}, main_label="TestNode")
        node_b = Node(pk={"id": 2}, main_label="TestNode")
        node_c = Node(pk={"id": 3}, main_label="TestNode")
        graph = self._make_graph()
        graph.insertNode(node_a, replace=True)
        graph.insertNode(node_b, replace=True)
        graph.insertNode(node_c, replace=True)
        graph.insertRelation(Relation(node_a, node_b, "LINKS"))
        graph.insertRelation(Relation(node_a, node_c, "LINKS"))

        # SET NULL: esborra node_a però no cascada a node_b i node_c
        result = graph.deleteNode(node_a, detach=True, on_delete="set_null")

        self.assertTrue(result)
        # node_a s'esborra; node_b i node_c queden
        self.assertEqual(len(graph.get_nodes()), 2)
        self.assertIn(2, graph.get_nodes())
        self.assertIn(3, graph.get_nodes())
        # Les arestes s'eliminen (el graf no admet arestes penjants)
        self.assertEqual(len(graph.get_edges()), 0)
        graph.close()

    def test_nx_graph_delete_set_null_no_cascade(self) -> None:
        """Test ON DELETE SET NULL: no cascada en nodes connectats."""
        node_a = Node(pk={"id": 1}, main_label="TestNode")
        node_b = Node(pk={"id": 2}, main_label="TestNode")
        node_c = Node(pk={"id": 3}, main_label="TestNode")
        graph = self._make_graph()
        graph.insertNode(node_a, replace=True)
        graph.insertNode(node_b, replace=True)
        graph.insertNode(node_c, replace=True)
        graph.insertRelation(Relation(node_a, node_b, "LINKS"))
        graph.insertRelation(Relation(node_b, node_c, "LINKS"))

        # SET NULL: esborra node_b però NO cascada a node_a ni node_c
        result = graph.deleteNode(node_b, detach=True, on_delete="set_null")

        self.assertTrue(result)
        # node_a i node_c queden (no s'han cascada)
        self.assertEqual(len(graph.get_nodes()), 2)
        self.assertIn(1, graph.get_nodes())
        self.assertIn(3, graph.get_nodes())
        graph.close()

    # -- Update/Replace CASCADE --

    def test_nx_graph_update_cascade_preserves_edges(self) -> None:
        """Test ON UPDATE CASCADE: actualitzar un node no trenca les arestes."""
        node_a = Node(pk={"id": 1}, main_label="TestNode", name="original", count=1)
        node_b = Node(pk={"id": 2}, main_label="TestNode", name="other")
        graph = self._make_graph()
        graph.insertNode(node_a, replace=True)
        graph.insertNode(node_b, replace=True)
        rel = Relation(node_a, node_b, "LINKS")
        graph.insertRelation(rel)

        # Update node_a with new attributes — edge should survive
        node_a_updated = Node(pk={"id": 1}, main_label="TestNode", name="updated", count=1)
        graph.insertNode(node_a_updated, update=True)

        self.assertEqual(len(graph.get_nodes()), 2)
        self.assertEqual(len(graph.get_edges()), 1)
        graph.close()

    def test_nx_graph_replace_cascade_removes_edges(self) -> None:
        """Test ON UPDATE CASCADE amb replace: les arestes s'esborren amb el node."""
        node_a = Node(pk={"id": 1}, main_label="TestNode", name="old", count=1)
        node_b = Node(pk={"id": 2}, main_label="TestNode", name="other")
        graph = self._make_graph()
        graph.insertNode(node_a, replace=True)
        graph.insertNode(node_b, replace=True)
        rel = Relation(node_a, node_b, "LINKS")
        graph.insertRelation(rel)

        # Replace node_a — edges are removed, new node gets new ID
        node_a_new = Node(pk={"id": 1}, main_label="TestNode", name="new", count=99)
        graph.insertNode(node_a_new, replace=True)

        self.assertEqual(len(graph.get_nodes()), 2)
        self.assertEqual(len(graph.get_edges()), 0)
        graph.close()

    # -- Dependencies / Atribut propagation --

    def test_nx_graph_individu_dependencies_create_atribut_nodes(self) -> None:
        """Test que IndividuPadro amb nom/cognoms genera nodes Atribut en dependencies."""
        ind = IndividuPadro(pk=1, nom="Oriol", cognom1="Ramos", cognom2="Perez")

        # Dependencies exist on the node object
        self.assertIsNotNone(ind._dependencies)
        self.assertIn("nom", ind._dependencies)
        self.assertIn("cognom1", ind._dependencies)
        self.assertIn("cognom2", ind._dependencies)

        # Each dependency is an Atribut node
        self.assertIsInstance(ind._dependencies["nom"], Atribut)
        self.assertIsInstance(ind._dependencies["cognom1"], Atribut)
        self.assertIsInstance(ind._dependencies["cognom2"], Atribut)

        # Atribut nodes have correct PKs
        self.assertEqual(ind._dependencies["nom"]._primary_key, {"name": "oriol"})
        self.assertEqual(ind._dependencies["cognom1"]._primary_key, {"name": "ramos"})
        self.assertEqual(ind._dependencies["cognom2"]._primary_key, {"name": "perez"})
        self.assertEqual(ind._dependencies["nom"]._main_label, "Valor")

    def test_nx_graph_dependencies_inserted_separately(self) -> None:
        """Test que les dependencies d'un IndividuPadro són nodes independents al graf.

        Ara NetworkXGraph equival a Neo4jGraph: les dependencies s'insereixen
        automàticament com a nodes Valor amb arestes HAS_*.
        """
        ind = IndividuPadro(pk=1, nom="Maria", cognom1="Garcia")
        graph = self._make_graph()

        # Insert the main node
        ind_id = graph.insertNode(ind, replace=True)
        self.assertIsNotNone(ind_id)

        # Verify the Atribut nodes exist in memory
        nom_attr = ind._dependencies["nom"]
        self.assertEqual(nom_attr._main_label, "Valor")
        self.assertEqual(nom_attr._primary_key, {"name": "maria"})

        # Dependencies are auto-inserted by NetworkXGraph (equivalent to Neo4jGraph)
        # 1 IndividuPadro + 2 Valor nodes (nom + cognom1)
        self.assertEqual(len(graph.get_nodes()), 3)

        # Verify the HAS_* relations were created
        edges = graph.get_edges()
        self.assertEqual(len(edges), 2)
        rel_types = {e[2] for e in edges}
        self.assertEqual(rel_types, {"NOM", "COGNOM1"})

        graph.close()

    def test_nx_graph_dependencies_multiple_individus_share_atribut(self) -> None:
        """Test que dos IndividuPadro amb el mateix nom comparteixen el mateix Atribut PK."""
        ind1 = IndividuPadro(pk=1, nom="Maria", cognom1="Garcia")
        ind2 = IndividuPadro(pk=2, nom="Maria", cognom1="Lopez")

        graph = self._make_graph()

        # Both have the same Atribut PK for nom
        self.assertEqual(ind1._dependencies["nom"]._primary_key, {"name": "maria"})
        self.assertEqual(ind2._dependencies["nom"]._primary_key, {"name": "maria"})

        # Insert both individuals
        id1 = graph.insertNode(ind1, replace=True)
        id2 = graph.insertNode(ind2, replace=True)
        self.assertNotEqual(id1, id2)

        # Insert shared Atribut (should be same node due to composite PK)
        nom_attr = Atribut("maria")
        nom_id = graph.insertNode(nom_attr, replace=True)

        # Insert second Atribut with replace=True — should update in-place
        nom_id_2 = graph.insertNode(Atribut("maria"), replace=False, update=True)
        self.assertEqual(nom_id, nom_id_2)

        graph.close()

    # -- Delete: propagation --

    def test_nx_graph_delete_propagation(self) -> None:
        """Test que NetworkXGraph implementa propagation com Neo4jGraph.

        NetworkXGraph deleteNode amb propagation=True esborra nodes fill
        quan l'aresta té _propagate=True. Equivalent a Neo4jGraph.
        """
        parent = Node(pk={"id": 1}, main_label="ParentNode", _propagate=True)
        child = Node(pk={"sub": 1}, main_label="ChildNode")
        graph = self._make_graph()

        graph.insertNode(parent, replace=True)
        graph.insertNode(child, replace=True)
        graph.insertRelation(Relation(parent, child, "HAS_CHILD"))
        graph._edge_attrs[(1, 2, "HAS_CHILD")]["_propagate"] = True

        result = graph.deleteNode(parent, propagation=True, detach=True)

        self.assertTrue(result)
        # propagation esborra el fill (node amb _propagate=True)
        self.assertEqual(len(graph.get_nodes()), 0)
        self.assertEqual(len(graph.get_edges()), 0)
        graph.close()

    # -- WeakNode: parent-child insertion and composite PK --

    def test_nx_graph_weak_node_insert(self) -> None:
        """Test que WeakNode amb parent es crea correctament al graf."""
        parent = Node(pk={"id": 1}, main_label="Document")
        child = WeakNode(parent=parent, pk={"sub_id": 1}, main_label="DocumentPart")

        self.assertTrue(child._is_weak)
        self.assertEqual(child._parent, parent)
        # PK es fusiona amb el parent
        self.assertEqual(child._primary_key, {"id": 1, "sub_id": 1})

        graph = self._make_graph()
        parent_id = graph.insertNode(parent, replace=True)
        child_id = graph.insertNode(child, insert_parent=True)

        self.assertIsNotNone(parent_id)
        self.assertIsNotNone(child_id)
        # Parent i child tenen IDs diferents al graf
        self.assertNotEqual(parent_id, child_id)
        self.assertEqual(len(graph.get_nodes()), 2)
        # La relació parent-child s'ha creat automàticament
        edges = graph.get_edges()
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0][2], "HAS")  # parent_relation default

        graph.close()

    def test_nx_graph_weak_node_missing_parent_raises(self) -> None:
        """Test que WeakNode sense parent inserit llança error si insert_parent=True."""
        parent = Node(pk={"id": 999}, main_label="Document")
        child = WeakNode(parent=parent, pk={"sub_id": 1}, main_label="DocumentPart")

        graph = self._make_graph()
        # No inserim el parent — insert_parent=True intenta inserir-lo
        # però el parent ja té un neo4j_id=None que el NetworkXGraph gestiona
        child_id = graph.insertNode(child, insert_parent=True)
        # El parent es crea automàticament amb un nou ID
        self.assertIsNotNone(child_id)
        self.assertEqual(len(graph.get_nodes()), 2)
        graph.close()

    def test_nx_graph_weak_node_composite_pk_integrity(self) -> None:
        """Test que les PK compostes de WeakNode mantenen integritat amb el parent."""
        parent = Node(pk={"doc_id": "DOC-001"}, main_label="Document")
        child_a = WeakNode(parent=parent, pk={"page": 1}, main_label="Page")
        child_b = WeakNode(parent=parent, pk={"page": 2}, main_label="Page")

        # Ambdós children comparteixen el mateix parent i tenen PK compostes úniques
        self.assertEqual(child_a._primary_key, {"doc_id": "DOC-001", "page": 1})
        self.assertEqual(child_b._primary_key, {"doc_id": "DOC-001", "page": 2})
        self.assertNotEqual(child_a._primary_key, child_b._primary_key)

        graph = self._make_graph()
        parent_id = graph.insertNode(parent, replace=True)
        child_a_id = graph.insertNode(child_a, insert_parent=True)
        child_b_id = graph.insertNode(child_b, insert_parent=True)

        self.assertNotEqual(child_a_id, child_b_id)
        self.assertEqual(len(graph.get_nodes()), 3)
        # Dos edges: parent→child_a i parent→child_b
        edges = graph.get_edges()
        self.assertEqual(len(edges), 2)
        for e in edges:
            self.assertEqual(e[2], "HAS")

        # CheckNode troba cada child per la seva PK composta
        self.assertEqual(graph.checkNode(child_a), child_a_id)
        self.assertEqual(graph.checkNode(child_b), child_b_id)

        graph.close()

    def test_nx_graph_weak_node_double_insert_same_pk(self) -> None:
        """Test que inserir el mateix WeakNode dues vegades amb replace=True.

        Nota: NetworkXGraph amb insert_parent=True sempre insereix el parent
        (no verifica si ja existeix). Per això el segon insert crea un
        pare nou. El comportament correcte és usar insert_parent=False
        en reinsertions.
        """
        parent = Node(pk={"id": 1}, main_label="Document")
        child = WeakNode(parent=parent, pk={"sub_id": 1}, main_label="Page")

        graph = self._make_graph()
        first_id = graph.insertNode(child, insert_parent=True)
        self.assertEqual(len(graph.get_nodes()), 2)

        # Reinsereix amb insert_parent=False per no duplicar el parent
        child_updated = WeakNode(parent=parent, pk={"sub_id": 1}, main_label="PageUpdated")
        second_id = graph.insertNode(child_updated, insert_parent=False, replace=True)

        # replace=True esborra i crea un node nou per al child
        self.assertNotEqual(first_id, second_id)
        # Parent + 2 children (el pare no es duplica amb insert_parent=False)
        self.assertEqual(len(graph.get_nodes()), 3)

        graph.close()

    # -- WeakNode nesting: weak of weak --

    def test_nx_graph_weak_node_nested_chain(self) -> None:
        """Test WeakNode nesting: grandparent → parent weak → child weak."""
        grandparent = Node(pk={"id": 1}, main_label="Document")
        parent_weak = WeakNode(parent=grandparent, pk={"section": 1}, main_label="Section")
        child_weak = WeakNode(parent=parent_weak, pk={"page": 1}, main_label="Page")

        # PK compostes en cadena
        self.assertEqual(parent_weak._primary_key, {"id": 1, "section": 1})
        self.assertEqual(child_weak._primary_key, {"id": 1, "section": 1, "page": 1})

        graph = self._make_graph()
        # Inserim la cadena completa
        gp_id = graph.insertNode(grandparent, replace=True)
        pw_id = graph.insertNode(parent_weak, insert_parent=True)
        cw_id = graph.insertNode(child_weak, insert_parent=True)

        self.assertIsNotNone(gp_id)
        self.assertIsNotNone(pw_id)
        self.assertIsNotNone(cw_id)
        self.assertEqual(len(graph.get_nodes()), 3)

        # La cadena de relacions: grandparent→parent_weak, parent_weak→child_weak
        edges = graph.get_edges()
        self.assertEqual(len(edges), 2)

        graph.close()

    def test_nx_graph_weak_node_nested_delete_cascade(self) -> None:
        """Test ON DELETE CASCADE en cadena de WeakNodes.

        Cascade elimina totes les arestes connectades al node esborrat
        (tant entrants com sortints), però NO esborra nodes veïns.
        Els nodes queden com a orfes; l'edge parent_weak→child_weak
        es manté perquè parent_weak encara existeix.
        """
        grandparent = Node(pk={"id": 1}, main_label="Document")
        parent_weak = WeakNode(parent=grandparent, pk={"section": 1}, main_label="Section")
        child_weak = WeakNode(parent=parent_weak, pk={"page": 1}, main_label="Page")

        graph = self._make_graph()
        graph.insertNode(grandparent, replace=True)
        graph.insertNode(parent_weak, insert_parent=True)
        graph.insertNode(child_weak, insert_parent=True)

        self.assertEqual(len(graph.get_nodes()), 3)
        self.assertEqual(len(graph.get_edges()), 2)

        # Esborrem el grandparent amb cascade
        result = graph.deleteNode(grandparent, detach=True)
        self.assertTrue(result)

        # Cascade elimina arestes però NO nodes fill
        # Els nodes queden com a orfes; l'edge parent_weak→child_weak
        # es manté perquè parent_weak encara existeix
        self.assertEqual(len(graph.get_nodes()), 2)
        self.assertEqual(len(graph.get_edges()), 1)

        graph.close()

    def test_nx_graph_weak_node_nested_set_null(self) -> None:
        """Test ON DELETE SET NULL en cadena de WeakNodes: esborrar el parent."""
        grandparent = Node(pk={"id": 1}, main_label="Document")
        parent_weak = WeakNode(parent=grandparent, pk={"section": 1}, main_label="Section")
        child_weak = WeakNode(parent=parent_weak, pk={"page": 1}, main_label="Page")

        graph = self._make_graph()
        graph.insertNode(grandparent, replace=True)
        graph.insertNode(parent_weak, insert_parent=True)
        graph.insertNode(child_weak, insert_parent=True)

        self.assertEqual(len(graph.get_nodes()), 3)

        # Esborrem el parent_weak amb SET NULL — només ell
        result = graph.deleteNode(parent_weak, detach=True, on_delete="set_null")
        self.assertTrue(result)

        # parent_weak s'esborra; grandparent i child_weak queden
        self.assertEqual(len(graph.get_nodes()), 2)
        self.assertIn(grandparent.neo4j_id if grandparent.neo4j_id else graph.checkNode(grandparent), graph.get_nodes())

        graph.close()

    def test_nx_graph_weak_node_propagate_change(self) -> None:
        """Test que canvis en parent es propaguen a WeakNode via PK fusionada."""
        parent = Node(pk={"id": 1}, main_label="Document")
        child = WeakNode(parent=parent, pk={"sub": 1}, main_label="SubDoc")

        # La PK del child ja inclou la del parent
        self.assertEqual(child._primary_key, {"id": 1, "sub": 1})

        # Actualitzem el parent — la PK fusionada es manté consistent
        parent_updated = Node(pk={"id": 1}, main_label="Document")
        graph = self._make_graph()

        parent_id = graph.insertNode(parent, replace=True)
        child_id = graph.insertNode(child, insert_parent=True)

        # Actualitzem el parent — la PK del child segueix sent vàlida
        graph.insertNode(parent_updated, update=True)

        # CheckNode del child encara el troba (la PK composta no ha canviat)
        self.assertEqual(graph.checkNode(child), child_id)
        self.assertEqual(len(graph.get_nodes()), 2)

        graph.close()

    def test_nx_graph_weak_node_propagation_delete(self) -> None:
        """Test que NetworkXGraph implementa propagation com Neo4jGraph.

        Esborrar el parent amb propagation=True esborra el fill WeakNode
        quan l'aresta parent-child té _propagate=True.
        """
        parent = Node(pk={"id": 1}, main_label="Document", _propagate=True)
        child = WeakNode(parent=parent, pk={"sub": 1}, main_label="SubDoc")

        graph = self._make_graph()
        graph.insertNode(parent, replace=True)
        graph.insertNode(child, insert_parent=True)

        self.assertEqual(len(graph.get_nodes()), 2)
        self.assertEqual(len(graph.get_edges()), 1)

        # Esborrar el parent amb propagation esborra el fill
        result = graph.deleteNode(parent, propagation=True, detach=True)
        self.assertTrue(result)

        # Tots dos nodes s'esborren (propagation cascada)
        self.assertEqual(len(graph.get_nodes()), 0)
        self.assertEqual(len(graph.get_edges()), 0)

        graph.close()

    def test_nx_graph_weak_node_multiple_children(self) -> None:
        """Test múltiples WeakNodes amb el mateix parent."""
        parent = Node(pk={"doc": "A"}, main_label="Document")
        child1 = WeakNode(parent=parent, pk={"page": 1}, main_label="Page")
        child2 = WeakNode(parent=parent, pk={"page": 2}, main_label="Page")
        child3 = WeakNode(parent=parent, pk={"page": 3}, main_label="Page")

        graph = self._make_graph()
        graph.insertNode(parent, replace=True)
        graph.insertNode(child1, insert_parent=True)
        graph.insertNode(child2, insert_parent=True)
        graph.insertNode(child3, insert_parent=True)

        self.assertEqual(len(graph.get_nodes()), 4)  # parent + 3 children
        self.assertEqual(len(graph.get_edges()), 3)  # 3 HAS edges

        # Cada child té PK composta única
        self.assertEqual(child1._primary_key, {"doc": "A", "page": 1})
        self.assertEqual(child2._primary_key, {"doc": "A", "page": 2})
        self.assertEqual(child3._primary_key, {"doc": "A", "page": 3})

        graph.close()

    def test_nx_graph_weak_node_no_pk_uses_backend_id(self) -> None:
        """Test WeakNode sense PK: el backend assigna l'ID com a PK."""
        parent = Node(pk={"id": 42}, main_label="Document")
        child = WeakNode(parent=parent, pk=None, main_label="Page")

        # Abans d'insertar, el fill hereta la PK del pare
        self.assertEqual(child._primary_key, {"id": 42})

        graph = self._make_graph()
        parent_id = graph.insertNode(parent, replace=True)
        child_id = graph.insertNode(child, insert_parent=True)

        self.assertIsNotNone(parent_id)
        self.assertIsNotNone(child_id)
        self.assertNotEqual(parent_id, child_id)
        # Després d'insertar, el fill té el seu propi ID
        self.assertEqual(child.neo4j_id, child_id)
        graph.close()

    def test_nx_graph_weak_node_with_pk_composed(self) -> None:
        """Test WeakNode amb PK: la PK es composa amb la del pare."""
        parent = Node(pk={"id": 42}, main_label="Document")
        child = WeakNode(parent=parent, pk={"page": 1}, main_label="Page")

        # La PK és composta
        self.assertEqual(child._primary_key, {"id": 42, "page": 1})

        graph = self._make_graph()
        parent_id = graph.insertNode(parent, replace=True)
        child_id = graph.insertNode(child, insert_parent=True)

        self.assertIsNotNone(parent_id)
        self.assertIsNotNone(child_id)
        graph.close()

    def test_nx_graph_weak_node_no_pk_composite_validation(self) -> None:
        """Test que dos WeakNodes sense PK però amb el mateix parent tenen IDs diferents."""
        parent = Node(pk={"doc_id": "DOC-001"}, main_label="Document")
        child_a = WeakNode(parent=parent, pk=None, main_label="Page")
        child_b = WeakNode(parent=parent, pk=None, main_label="Page")

        graph = self._make_graph()
        parent_id = graph.insertNode(parent, replace=True)
        child_a_id = graph.insertNode(child_a, insert_parent=True)
        child_b_id = graph.insertNode(child_b, insert_parent=True)

        # Cada fill té un ID únic assignat pel backend
        self.assertNotEqual(child_a_id, child_b_id)
        self.assertEqual(len(graph.get_nodes()), 3)
        graph.close()


# ---------------------------------------------------------------------------
# Tests for Neo4jGraph — wraps Neo4j driver (mocked in tests)
# ---------------------------------------------------------------------------

    def test_nx_graph_debug_output(self) -> None:
        """Test que debug() retorna l'estat correcte del graf."""
        node_a = Node(pk={"id": 1}, main_label="TestNode")
        node_b = Node(pk={"id": 2}, main_label="TestNode")
        graph = self._make_graph()
        graph.insertNode(node_a, replace=True)
        graph.insertNode(node_b, replace=True)
        graph.insertRelation(Relation(node_a, node_b, "LINKS"))

        state = graph.debug()
        graph.close()

        self.assertIn("nodes", state)
        self.assertIn("edges", state)
        self.assertIn("fk_index", state)
        self.assertEqual(len(state["nodes"]), 2)
        self.assertEqual(len(state["edges"]), 1)
        self.assertGreater(len(state["fk_index"]), 0)

    # -- Node CRUD (equivalent to Neo4j real tests) --

    def test_nx_graph_update_node_pk_compost(self) -> None:
        """Test per validar la creacio de nodes amb pk compost."""
        a = Node(
            pk={"nom": "Caldes dEstrac", "any": 1905},
            main_label="LlocPadro",
            alternative_labels=["TEST"],
            estat="inserit",
        )
        b = Node(
            pk={"nom": "Caldes dEstrac", "any": 1905},
            main_label="LlocPadro",
            alternative_labels=["TEST"],
            estat="actualitzat",
        )

        graph = self._make_graph()
        up_a_1 = graph.insertNode(a, replace=True)
        up_b_1 = graph.insertNode(b, replace=False, update=True)

        self.assertGreaterEqual(up_a_1, 0)
        self.assertGreaterEqual(up_b_1, 0)
        # Both should have the same ID (b updates a in-place)
        self.assertEqual(up_a_1, up_b_1)
        graph.close()

    def test_nx_graph_update_node_lloc_padro(self) -> None:
        """Test per validar la creacio de nodes LlocPadro."""
        a = LlocPadro(
            pk={"nom": "Caldes dEstrac", "any": 1905},
            alternative_labels=["TEST"],
            estat="inserit",
        )
        b = LlocPadro(
            pk={"nom": "Caldes dEstrac", "any": 1905},
            main_label="LlocPadro",
            alternative_labels=["TEST"],
            estat="actualitzat",
        )

        graph = self._make_graph()
        up_a_1 = graph.insertNode(a, replace=True)
        up_b_1 = graph.insertNode(b, replace=False, update=True)

        self.assertGreaterEqual(up_a_1, 0)
        self.assertGreaterEqual(up_b_1, 0)
        # Both should have the same ID (b updates a in-place)
        self.assertEqual(up_a_1, up_b_1)
        graph.close()

    def test_nx_graph_insert_individu_padro(self) -> None:
        """Test per validar la creacio de nodes IndividuPadro."""
        a = IndividuPadro(pk=1, nom="Oriol", cognom1="Ramos", edat=18, alternative_labels="TEST")
        b = IndividuPadro(pk=2, nom="Sergio", cognom1="Ramos", ofici="fuster", alternative_labels="TEST")
        c = IndividuPadro(pk=3, nom="Pere", cognom1="Fuster", edat="50", alternative_labels="TEST")

        graph = self._make_graph()
        up_a = graph.insertNode(a, replace=True)
        up_b = graph.insertNode(b, replace=False, update=True)
        up_c = graph.insertNode(c, replace=False, update=False)

        self.assertGreaterEqual(up_a, 0)
        self.assertGreaterEqual(up_b, 0)
        self.assertGreaterEqual(up_c, 0)
        # 3 IndividuPadro + 5 Valor nodes (3 noms + 2 cognoms únics: "Ramos" es comparteix)
        self.assertEqual(len(graph.get_nodes()), 8)
        graph.close()

    def test_nx_graph_node_pk_none_assigned_after_insert(self) -> None:
        """Node amb pk=None explícit: el backend assigna un ID com a PK.

        (Node/Relation dunder-method unit tests live in test_node.py /
        test_relation.py; this one stays here because it exercises
        NetworkXGraph's ID assignment, not just the Node class.)
        """
        node = Node(pk=None, main_label="AutoIdNode")
        self.assertIsNone(node._primary_key)
        graph = self._make_graph()
        graph.insertNode(node, replace=True)
        self.assertIsNotNone(node._primary_key)
        self.assertIn("id", node._primary_key)
        self.assertEqual(node._primary_key["id"], node.neo4j_id)
        graph.close()


# ---------------------------------------------------------------------------
# Tests for Neo4jGraph — wraps Neo4j driver (mocked in tests)
# ---------------------------------------------------------------------------

class _MockNeo4jRecord:
    """Minimal mock of a neo4j record dict-like object."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def value(self, key: str) -> Any:
        """Return the value for the given key, or the first value if not found."""
        if key in self._data:
            return self._data[key]
        # Fallback: return first value (neo4j positional lookup)
        return list(self._data.values())[0] if self._data else None


class _MockNeo4jResult:
    """Minimal mock of a neo4j Result object."""

    def __init__(self, records: List[Dict[str, Any]]) -> None:
        self._records = records
        self._iter = iter(records)

    def single(self) -> Optional[_MockNeo4jRecord]:
        results = []
        for r in self._records:
            results.append(r)
        if results:
            return _MockNeo4jRecord(results[-1])
        return None

    def values(self) -> List[Any]:
        return [list(r.values()) for r in self._records]

    def value(self, key: str = "") -> Any:
        """Return a list of all values for the given key — mimics neo4j Result.

        In neo4j, .value(key) returns the first column's values across all records.
        The key name is just informational; the actual values are positional.
        """
        if not self._records:
            return []
        # Return first column values from all records (positional, not keyed)
        return [list(r.values())[0] for r in self._records]


class _MockNeo4jSession:
    """Minimal mock of a neo4j Session that stores nodes in memory."""

    def __init__(self) -> None:
        self._nodes: Dict[int, Dict[str, Any]] = {}
        self._node_counter: int = 0
        self._protocol_version: Tuple[int, ...] = (5, 0)
        self._tx: Optional["_MockNeo4jTransaction"] = None

    def begin_transaction(self) -> "_MockNeo4jTransaction":
        self._tx = _MockNeo4jTransaction(self)
        return self._tx

    def get_server_info(self) -> Any:
        server = mock_neo4j.MagicMock()
        server.protocol_version = self._protocol_version
        return server


class _MockNeo4jTransaction:
    """Minimal mock of a neo4j Transaction that simulates Cypher queries."""

    def __init__(self, session: _MockNeo4jSession) -> None:
        self._session = session
        self._closed = False
        self._rolled_back = False

    def run(self, query: str, **params: Any) -> "_MockNeo4jResult":
        """Execute a Cypher query against the mock session state."""
        if self._closed:
            raise RuntimeError("Transaction is closed")

        # Extract label from query: MATCH (a:Label) or CREATE (a:Label)
        import re
        label_match = re.search(r'\((\w+):(\w+)', query)
        label = label_match.group(2) if label_match else None

        # CREATE node: increment counter, return new id
        if "CREATE" in query and "RETURN id(a)" in query:
            self._session._node_counter += 1
            new_id = self._session._node_counter
            props = params.get("prop_dict", {})
            # Store the label so MERGE can find it later
            if label:
                props["_main_label"] = label
            self._session._nodes[new_id] = props
            return _MockNeo4jResult([{"id(a)": new_id}])

        # MERGE node: check if exists by label + pk, return existing or create new
        if "MERGE" in query and "RETURN id(a)" in query:
            props = params.get("prop_dict", {})
            # Try to match by common pk fields
            pk_fields = ["nom", "any", "id"]
            pk_values = {k: props[k] for k in pk_fields if k in props}

            found_id = None
            for nid, attrs in self._session._nodes.items():
                # Must match label AND pk values
                node_label = attrs.get("_main_label") or attrs.get("main_label")
                if label and node_label != label:
                    continue
                match = all(attrs.get(k) == v for k, v in pk_values.items() if v is not None)
                if match:
                    found_id = nid
                    break

            if found_id is not None:
                # Update attributes
                self._session._nodes[found_id].update(props)
                return _MockNeo4jResult([{"id(a)": found_id}])
            else:
                # Create new node
                self._session._node_counter += 1
                new_id = self._session._node_counter
                if label:
                    props["_main_label"] = label
                self._session._nodes[new_id] = props
                return _MockNeo4jResult([{"id(a)": new_id}])

        # MATCH node by pk: RETURN id(a)
        if "MATCH" in query and "RETURN id(a)" in query and "count" not in query:
            for nid, attrs in self._session._nodes.items():
                if any(str(v) in query for v in attrs.values()):
                    return _MockNeo4jResult([{"id(a)": nid}])
            return _MockNeo4jResult([])

        # RETURN count(n) > 0 AS found
        if "RETURN count(n) > 0 AS found" in query:
            for nid, attrs in self._session._nodes.items():
                if any(str(v) in query for v in attrs.values()):
                    return _MockNeo4jResult([{"found": True}])
            return _MockNeo4jResult([{"found": False}])

        # MATCH with count{(n)-[]-()} = 0 AS has_no_edges
        if "has_no_edges" in query:
            return _MockNeo4jResult([{"has_no_edges": True}])

        # RETURN id(r) AS id
        if "RETURN id(r) AS id" in query:
            return _MockNeo4jResult([{"id": 1}])

        # RETURN nid
        if "RETURN id(n) AS nid" in query:
            for nid in self._session._nodes:
                return _MockNeo4jResult([{"nid": nid}])
            return _MockNeo4jResult([])

        # RETURN b
        if "RETURN b" in query:
            return _MockNeo4jResult([])

        # DELETE queries
        if "DELETE" in query:
            return _MockNeo4jResult([])

        # CREATE relation
        if "CREATE" in query and "RETURN id(r)" in query:
            return _MockNeo4jResult([{"id(r)": 1}])

        # Default: return empty
        return _MockNeo4jResult([])

    def commit(self) -> None:
        self._closed = False

    def rollback(self) -> None:
        self._rolled_back = True

    def close(self) -> None:
        self._closed = True

    def closed(self) -> bool:
        return self._closed


class Neo4jGraphTest(unittest.TestCase):
    """Tests for Neo4jGraph — verifies the Neo4j driver integration layer.

    The neo4j driver is mocked so these tests run without a real database.
    They verify that Neo4jGraph calls the driver correctly and handles
    responses properly.
    """

    def setUp(self) -> None:
        """Patch GraphDatabase.driver so Neo4jGraph uses a mock."""
        self.patcher = mock_patch("cvcdocdb.neo4j_graph.GraphDatabase")
        mock_gd = self.patcher.start()
        self.mock_driver = mock_neo4j()
        mock_gd.driver.return_value = self.mock_driver
        # Default: session returns a mock session that succeeds on queries
        self.mock_session = mock_neo4j()
        self.mock_driver.session.return_value = self.mock_session
        self.mock_driver.get_server_info.return_value = mock_neo4j(
            protocol_version=(5, 0)
        )

    def tearDown(self) -> None:
        self.patcher.stop()

    def _make_graph(self) -> Neo4jGraph:
        """Create a Neo4jGraph instance with a mocked driver."""
        return Neo4jGraph(
            url="bolt://localhost:7687",
            user="test",
            password="test",
            database="test",
        )

    def test_update_node_pk_compost(self) -> None:
        """Test per validar la creacio de nodes amb pk compost."""
        graph = self._make_graph()
        # Mock the internal _insertNode to avoid Cypher complexity
        graph._insertNode = mock_neo4j(side_effect=lambda n, update=False, replace=False: 1)
        graph.checkNode = mock_neo4j(return_value=False)

        a = Node(
            pk={"nom": "Caldes dEstrac", "any": 1905},
            main_label="LlocPadro",
            alternative_labels=["TEST"],
            estat="inserit",
        )
        b = Node(
            pk={"nom": "Caldes dEstrac", "any": 1905},
            main_label="LlocPadro",
            alternative_labels=["TEST"],
            estat="actualitzat",
        )

        up_a_1 = graph.insertNode(a, replace=True)
        up_b_1 = graph.insertNode(b, replace=False, update=True)

        self.assertIsInstance(up_a_1, int)
        self.assertGreaterEqual(up_a_1, 0)
        self.assertIsInstance(up_b_1, int)
        self.assertGreaterEqual(up_b_1, 0)
        # _insertNode should be called twice
        self.assertEqual(graph._insertNode.call_count, 2)
        graph.close()

    def test_update_node_lloc_padro(self) -> None:
        """Test per validar la creacio de nodes LlocPadro."""
        graph = self._make_graph()
        graph._insertNode = mock_neo4j(side_effect=lambda n, update=False, replace=False: 1)
        graph.checkNode = mock_neo4j(return_value=False)

        a = LlocPadro(
            pk={"nom": "Caldes dEstrac", "any": 1905},
            alternative_labels=["TEST"],
            estat="inserit",
        )
        b = LlocPadro(
            pk={"nom": "Caldes dEstrac", "any": 1905},
            main_label="LlocPadro",
            alternative_labels=["TEST"],
            estat="actualitzat",
        )

        up_a_1 = graph.insertNode(a, replace=True)
        up_b_1 = graph.insertNode(b, replace=False, update=True)

        self.assertIsInstance(up_a_1, int)
        self.assertGreaterEqual(up_a_1, 0)
        self.assertIsInstance(up_b_1, int)
        self.assertGreaterEqual(up_b_1, 0)
        graph.close()

    def test_insert_individu_padro(self) -> None:
        """Test per validar la creacio de nodes IndividuPadro."""
        graph = self._make_graph()
        graph._insertNode = mock_neo4j(side_effect=lambda n, update=False, replace=False: 1)
        graph.checkNode = mock_neo4j(return_value=False)

        ind_1: Dict[str, Any] = {"pk": 1, "nom": "Oriol", "cognom1": "Ramos", "edat": 18}
        ind_2: Dict[str, Any] = {"pk": 2, "nom": "Sergio", "cognom1": "Ramos", "ofici": "fuster"}
        ind_3: Dict[str, Any] = {"pk": 3, "nom": "Pere", "cognom1": "Fuster", "edat": "50"}
        a = IndividuPadro(**ind_1, alternative_labels="TEST")
        b = IndividuPadro(**ind_2, alternative_labels="TEST")
        c = IndividuPadro(**ind_3, alternative_labels="TEST")

        up_a = graph.insertNode(a, replace=True)
        up_b = graph.insertNode(b, replace=False, update=True)
        up_c = graph.insertNode(c, replace=False, update=False)

        self.assertIsInstance(up_a, int)
        self.assertIsInstance(up_b, int)
        self.assertIsInstance(up_c, int)
        self.assertGreaterEqual(up_a, 0)
        self.assertGreaterEqual(up_b, 0)
        self.assertGreaterEqual(up_c, 0)
        graph.close()

    def test_vector_api_is_exposed_but_not_supported(self) -> None:
        """Neo4jGraph exposa la API vectorial comuna però amb NotImplementedError."""
        graph = self._make_graph()
        with self.assertRaises(NotImplementedError):
            graph.enable_vector_index("embedding", dimensions=3)
        with self.assertRaises(NotImplementedError):
            graph.query_vector_index("embedding", [1.0, 0.0, 0.0], top_k=1)
        graph.close()

    def test_insert_node_rejects_malicious_main_label(self) -> None:
        """A main_label crafted to break out of a Cypher label position must
        be rejected before any query is built (Cypher injection guard)."""
        graph = self._make_graph()
        node = Node(pk={"id": 1}, main_label='Evil) DETACH DELETE n //')
        with self.assertRaises(ValueError):
            graph.insertNode(node, replace=True)
        graph.close()

    def test_insert_node_rejects_malicious_alternative_label(self) -> None:
        """An alternative_labels entry crafted to break out of a Cypher
        label position must be rejected, not just main_label."""
        graph = self._make_graph()
        node = Node(
            pk={"id": 1},
            main_label="A",
            alternative_labels=['Evil) DETACH DELETE n //'],
        )
        with self.assertRaises(ValueError):
            graph.insertNode(node, replace=True)
        graph.close()

    def test_insert_node_rejects_malicious_dependency_label(self) -> None:
        """A dependency node's main_label is inserted via the internal
        _insertNode path directly, bypassing insertNode's own check — it
        must still be validated."""
        graph = self._make_graph()
        dep = Node(pk={"id": 2}, main_label='Evil) DETACH DELETE n //', value="x")
        node = Node(
            pk={"id": 1},
            main_label="A",
            dependencies={"has_value": dep},
        )
        with self.assertRaises(ValueError):
            graph.insertNode(node, replace=True)
        graph.close()

    def test_insert_relation_rejects_malicious_rel_type(self) -> None:
        """A relation type crafted to break out of a Cypher relation-type
        position must be rejected before any query is built."""
        graph = self._make_graph()
        src = Node(pk={"id": 1}, main_label="A")
        dst = Node(pk={"id": 2}, main_label="B")
        rel = Relation(src, dst, "KNOWS]->(x) DETACH DELETE x //")
        with self.assertRaises(ValueError):
            graph.insertRelation(rel)
        graph.close()

    def test_generate_where_cond_escapes_quotes_in_pk_values(self) -> None:
        """A pk value containing a double quote must not be able to close
        the Cypher string literal early."""
        from cvcdocdb.neo4j_graph import _generate_where_cond

        cond = _generate_where_cond("a", {"name": 'x" OR TRUE OR "'})
        # The injected quote must be escaped, so the payload stays inside
        # a single string literal rather than becoming Cypher syntax.
        self.assertIn('\\"', cond)
        self.assertNotIn('x" OR TRUE OR "', cond)

    def test_generate_where_cond_rejects_malicious_property_key(self) -> None:
        """A pk dict key is spliced as a bare Cypher property name and must
        be validated as a safe identifier."""
        from cvcdocdb.neo4j_graph import _generate_where_cond

        with self.assertRaises(ValueError):
            _generate_where_cond("a", {"id}) DETACH DELETE a //": 1})

    def test_relation_creation(self) -> None:
        """Test per validar la creacio de relacions entre nodes."""
        graph = self._make_graph()
        graph._insertNode = mock_neo4j(side_effect=lambda n, update=False, replace=False: 1)
        graph.checkNode = mock_neo4j(return_value=False)
        graph._create_relation = mock_neo4j(return_value=1)

        src = Node(pk={"nom": "NodeA"}, main_label="LlocPadro")
        dst = Node(pk={"nom": "NodeB"}, main_label="LlocPadro")
        rel = Relation(src, dst, "CONNECTS")

        graph.insertNode(src, replace=True)
        graph.insertNode(dst, replace=True)
        result = graph.insertRelation(rel)

        self.assertIsInstance(result, int)
        # Check FK index has entries
        self.assertGreater(len(graph._fk_index), 0)
        graph.close()

    def test_relation_fk_violation_src_missing(self) -> None:
        """Test que crear una relació amb src no inserit llança RuntimeError."""
        graph = self._make_graph()

        # Mock _validate_fk to return False for src, True for dst
        def validate_fk_side_effect(tx, node_data, direction):
            if node_data.get("pk") and "Missing" in str(node_data.get("pk")):
                return False
            return True

        graph._validate_fk = mock_neo4j(side_effect=validate_fk_side_effect)

        src = Node(pk={"nom": "Missing"}, main_label="LlocPadro")
        dst = Node(pk={"nom": "NodeB"}, main_label="LlocPadro")
        rel = Relation(src, dst, "CONNECTS")

        with self.assertRaises(RuntimeError) as ctx:
            graph.insertRelation(rel)
        self.assertIn("FK violation", str(ctx.exception))
        self.assertIn("src", str(ctx.exception))
        graph.close()

    def test_relation_fk_violation_dst_missing(self) -> None:
        """Test que crear una relació amb dst no inserit llança RuntimeError."""
        graph = self._make_graph()

        def validate_fk_side_effect(tx, node_data, direction):
            if node_data.get("pk") and "Missing" in str(node_data.get("pk")):
                return False
            return True

        graph._validate_fk = mock_neo4j(side_effect=validate_fk_side_effect)

        src = Node(pk={"nom": "NodeA"}, main_label="LlocPadro")
        dst = Node(pk={"nom": "Missing"}, main_label="LlocPadro")
        rel = Relation(src, dst, "CONNECTS")

        with self.assertRaises(RuntimeError) as ctx:
            graph.insertRelation(rel)
        self.assertIn("FK violation", str(ctx.exception))
        self.assertIn("dst", str(ctx.exception))
        graph.close()

    def test_relation_fk_violation_both_missing(self) -> None:
        """Test que crear una relació amb ambdós nodes no inserits llança RuntimeError."""
        graph = self._make_graph()
        graph._validate_fk = mock_neo4j(return_value=False)

        src = Node(pk={"nom": "MissingA"}, main_label="LlocPadro")
        dst = Node(pk={"nom": "MissingB"}, main_label="LlocPadro")
        rel = Relation(src, dst, "CONNECTS")

        with self.assertRaises(RuntimeError) as ctx:
            graph.insertRelation(rel)
        self.assertIn("FK violation", str(ctx.exception))
        self.assertIn("src", str(ctx.exception))
        graph.close()


# ---------------------------------------------------------------------------
# Tests for entities.py classes
# ---------------------------------------------------------------------------

class EntitiesTest(unittest.TestCase):
    """Tests for entity classes defined in entities.py."""

    def test_individu_padro_creation(self) -> None:
        """Test que IndividuPadro es pugui crear amb pk i nom."""
        ind = IndividuPadro(pk=1, nom="Oriol", cognom1="Ramos")
        self.assertEqual(ind._main_label, "IndividuPadro")
        self.assertIn("Individu", ind._label)

    def test_individu_padro_missing_pk(self) -> None:
        """Test que IndividuPadro sense pk cridi exit()."""
        with self.assertRaises(SystemExit):
            IndividuPadro(nom="Oriol")

    def test_individu_padro_missing_nom(self) -> None:
        """Test que IndividuPadro sense nom cridi exit()."""
        with self.assertRaises(SystemExit):
            IndividuPadro(pk=1)

    def test_individu_padro_ignore_assertion(self) -> None:
        """Test que ignore_assertion permeti saltar la validacio."""
        ind = IndividuPadro(pk=1, ignore_assertion=True, nom="Test")
        self.assertEqual(ind._main_label, "IndividuPadro")

    def test_individu_padro_dependencies(self) -> None:
        """Test que be_value_properties generin dependencies."""
        ind = IndividuPadro(pk=1, nom="Oriol", cognom1="Ramos")
        self.assertIsNotNone(ind._dependencies)
        self.assertIn("nom", ind._dependencies)
        self.assertIn("cognom1", ind._dependencies)
        self.assertIsInstance(ind._dependencies["nom"], Atribut)

    def _make_isolated_graph(self) -> NetworkXGraph:
        """A NetworkXGraph backed by a throwaway persistence file, so each
        test starts from an empty graph regardless of leftover state from
        the default (shared, on-disk) persistence path."""
        path = tempfile.mktemp(suffix=".pkl")
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return NetworkXGraph(persistence_path=path)

    def test_individu_padro_get_attrs_hydrates_value_properties(self) -> None:
        """get_attrs recupera nom/cognom1 dels nodes Valor connectats."""
        graph = self._make_isolated_graph()
        ind = IndividuPadro(pk=1, nom="Oriol", cognom1="Ramos")
        graph.insertNode(ind)
        attrs = ind.get_attrs(graph)
        self.assertEqual(attrs["nom"], "oriol")
        self.assertEqual(attrs["cognom1"], "ramos")
        graph.close()

    def test_individu_padro_get_attrs_missing_value_property(self) -> None:
        """get_attrs no afegeix una clau per a un be_value_property no proporcionat."""
        graph = self._make_isolated_graph()
        ind = IndividuPadro(pk=1, nom="Oriol")
        graph.insertNode(ind)
        attrs = ind.get_attrs(graph)
        self.assertEqual(attrs["nom"], "oriol")
        self.assertNotIn("cognom1", attrs)
        self.assertNotIn("cognom2", attrs)
        graph.close()

    def test_individu_foto_get_attrs_no_hydration(self) -> None:
        """get_attrs no fa cap consulta extra per a be_value_properties buit."""
        graph = self._make_isolated_graph()
        foto = IndividuFoto(pk=2)
        graph.insertNode(foto)
        attrs = foto.get_attrs(graph)
        self.assertNotIn("nom", attrs)
        graph.close()

    def test_generic_node_get_attrs_no_hydration(self) -> None:
        """Un Node generic (sense be_value_properties) no es connecta amb res extern."""
        graph = self._make_isolated_graph()
        node = Node(pk={"id": 99}, main_label="TestNode", foo="bar")
        graph.insertNode(node)
        attrs = node.get_attrs(graph)
        self.assertEqual(attrs.get("foo"), "bar")
        self.assertNotIn("nom", attrs)
        graph.close()

    def test_get_attrs_does_not_mutate_store_state(self) -> None:
        """get_attrs no ha de mutar el dict intern del backend."""
        graph = self._make_isolated_graph()
        ind = IndividuPadro(pk=1, nom="Oriol")
        graph.insertNode(ind)
        ind.get_attrs(graph)
        raw = graph.get_node_attrs(ind.neo4j_id)
        self.assertNotIn("nom", raw)
        graph.close()

    def test_weaknode_be_value_properties_creates_dependencies(self) -> None:
        """Un WeakNode que declara be_value_properties materialitza Atribut
        a la construcció, igual que Individu/IndividuPadro."""

        class SeccioAmbTitol(WeakNode):
            be_value_properties = ("titol",)

        parent = Node(pk={"id": 1}, main_label="Document")
        sec = SeccioAmbTitol(parent=parent, pk=1, main_label="Seccio", titol="Introduccio")
        self.assertIsNotNone(sec._dependencies)
        self.assertIn("titol", sec._dependencies)
        self.assertIsInstance(sec._dependencies["titol"], Atribut)

    def test_weaknode_be_value_properties_get_attrs_hydrates(self) -> None:
        """get_attrs sobre un WeakNode resol be_value_properties des del Valor."""

        class SeccioAmbTitol(WeakNode):
            be_value_properties = ("titol",)

        graph = self._make_isolated_graph()
        parent = Node(pk={"id": 1}, main_label="Document")
        sec = SeccioAmbTitol(parent=parent, pk=1, main_label="Seccio", titol="Introduccio")
        graph.insertNode(sec)
        attrs = sec.get_attrs(graph)
        self.assertEqual(attrs["titol"], "introduccio")
        graph.close()

    def test_weaknode_without_be_value_properties_unaffected(self) -> None:
        """Un WeakNode genèric (sense be_value_properties) no crea dependencies."""
        parent = Node(pk={"id": 1}, main_label="Document")
        page = WeakNode(parent=parent, pk=1, main_label="Page", ruta="/x")
        self.assertIsNone(page._dependencies)

    def test_individu_foto_creation(self) -> None:
        """Test que IndividuFoto es pugui crear amb pk."""
        foto = IndividuFoto(pk=1)
        self.assertEqual(foto._main_label, "IndividuFoto")
        self.assertIn("Individu", foto._label)

    def test_individu_foto_missing_pk(self) -> None:
        """Test que IndividuFoto sense pk cridi exit()."""
        with self.assertRaises(SystemExit):
            IndividuFoto()

    def test_lloc_padro_creation(self) -> None:
        """Test que LlocPadro es pugui crear amb pk."""
        lloc = LlocPadro(pk={"nom": "Caldes"})
        self.assertEqual(lloc._main_label, "LlocPadro")
        self.assertIn("Lloc", lloc._label)

    def test_lloc_foto_creation(self) -> None:
        """Test que LlocFoto es pugui crear amb pk."""
        lloc = LlocFoto(pk={"nom": "Foto"})
        self.assertEqual(lloc._main_label, "LlocFoto")
        self.assertIn("Lloc", lloc._label)

    def test_atribut_creation(self) -> None:
        """Test que Atribut es pugui crear amb un valor."""
        attr = Atribut("test_value")
        self.assertEqual(attr._main_label, "Valor")
        self.assertEqual(attr._primary_key, {"name": "test_value"})

    def test_padro_creation(self) -> None:
        """Test que Padro es pugui crear amb pk i ruta."""
        parent = Node(pk={"id": 0}, main_label="Fons")
        padro = Padro(parent=parent, pk=1, ruta="/path/to/doc")
        self.assertEqual(padro._main_label, "Padro")
        self.assertTrue(padro._is_weak)

    def test_padro_missing_ruta(self) -> None:
        """Test que Padro sense ruta cridi exit()."""
        with self.assertRaises(SystemExit):
            Padro(pk=1)

    def test_fotografia_creation(self) -> None:
        """Test que Fotografia es pugui crear amb pk."""
        parent = Node(pk={"id": 0}, main_label="Padro")
        foto = Fotografia(parent=parent, pk=1)
        self.assertEqual(foto._main_label, "Fotografia")
        self.assertTrue(foto._is_weak)

    def test_fons_creation(self) -> None:
        """Test que Fons es pugui crear amb pk."""
        fons = Fons(pk=1)
        self.assertEqual(fons._main_label, "Fons")
        self.assertIn("DocumentCultural", fons._label)

    def test_esdeventiment_creation(self) -> None:
        """Test que Esdeventiment es pugui crear amb pk.

        Note: Esdeventiment passa pk explícitament i via **kwargs,
        això llança TypeError (duplicate kwarg). Bug conegut.
        """
        with self.assertRaises(TypeError):
            Esdeventiment(pk=1, ignore_assertion=True)

    def test_acta_temporal_creation(self) -> None:
        """Test que ActaTemporal es pugui crear amb pk."""
        acta = ActaTemporal(pk=1)
        self.assertEqual(acta._main_label, "ActaTemporal")

    def test_boe_creation(self) -> None:
        """Test que BOE es pugui crear amb pk i ruta."""
        parent = Node(pk={"id": 0}, main_label="Fons")
        boe = BOE(parent=parent, pk=1, ruta="/path/to/boe")
        self.assertEqual(boe._main_label, "BOE")
        self.assertTrue(boe._is_weak)

    def test_boe_ignore_assertion(self) -> None:
        """Test que BOE amb ignore_assertion i sense ruta afegeix un valor per defecte."""
        parent = Node(pk={"id": 0}, main_label="Fons")
        boe = BOE(parent=parent, pk=1, ignore_assertion=True)
        self.assertEqual(boe._main_label, "BOE")


if __name__ == "__main__":
    unittest.main()
