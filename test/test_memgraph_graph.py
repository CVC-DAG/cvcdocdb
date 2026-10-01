"""Backend Memgraph (`MemgraphGraph`).

`MemgraphGraph` reutilitza la lògica de `Neo4jGraph` (Memgraph parla Bolt i
Cypher), de manera que la política de propagació de canvis és la mateixa:

- **Inserció**: un WeakNode insereix el seu pare (``insert_parent``) i crea
  la relació pare→fill amb ``_propagate=TRUE``; sense pare, falla i no deixa
  res; les claus del fill han de referenciar les del pare; les dependències
  (``Valor``) es materialitzen; una clau duplicada es rebutja.
- **Actualització**: ``update=True`` fa MERGE i fusiona atributs;
  ``replace=True`` esborra el node existent amb propagació (els WeakNodes
  fills desapareixen) i en crea un de nou.
- **Esborrat**: RESTRICT (per defecte) es nega si hi ha fills o arestes;
  ``propagation=True`` esborra recursivament els fills WeakNode; ``detach``
  (CASCADE) treu les arestes i conserva els veïns; ``on_delete="set_null"``.
- **Relacions**: validació de FK, ``update``/``replace``.

Els tests `slow` necessiten un Memgraph (``MEMGRAPH_URL``; el conftest
n'arrenca un amb Docker si cal). Els escenaris de propagació també
s'executen sobre Neo4j (``NEO4J_DEV_URL``), si n'hi ha, i es comprova que
els dos backends deixen exactament el mateix graf i els mateixos errors.
"""

from __future__ import annotations

import os
import unittest
import warnings
from typing import Any, Callable, Dict, List, Optional
from unittest import mock

import pytest

from cvcdocdb.base import Node
from cvcdocdb.neo4j_enterprise import EnterpriseFeatureError

from test import test_graph_store_contract as _contract
from test import propagation_scenarios as ps
from test.propagation_scenarios import SCENARIOS, Outcome, document_tree, run_scenario, snapshot


def _memgraph_config() -> Optional[Dict[str, Any]]:
    url = os.environ.get("MEMGRAPH_URL")
    if not url:
        return None
    return {
        "url": url,
        "user": os.environ.get("MEMGRAPH_USER", ""),
        "password": os.environ.get("MEMGRAPH_PASSWORD", ""),
        "database": os.environ.get("MEMGRAPH_DATABASE") or None,
    }


def _neo4j_config() -> Optional[Dict[str, Any]]:
    url = os.environ.get("NEO4J_DEV_URL")
    if not url:
        return None
    return {
        "url": url,
        "user": os.environ.get("NEO4J_DEV_USER", "neo4j"),
        "password": os.environ.get("NEO4J_DEV_PASSWORD", ""),
        "database": os.environ.get("NEO4J_DEV_DATABASE") or None,
    }


def _fresh_memgraph() -> Any:
    from cvcdocdb.memgraph_graph import MemgraphGraph

    config = _memgraph_config()
    if config is None:
        raise unittest.SkipTest("Sense MEMGRAPH_URL: cal un Memgraph real")
    graph = MemgraphGraph(**config)
    graph.query("MATCH (n) DETACH DELETE n")
    return graph


def _fresh_neo4j() -> Any:
    from cvcdocdb.neo4j_graph import Neo4jGraph

    config = _neo4j_config()
    if config is None:
        return None
    graph = Neo4jGraph(**config)
    graph.query("MATCH (n) DETACH DELETE n")
    return graph


# ---------------------------------------------------------------------------
# Sense servidor
# ---------------------------------------------------------------------------


def _mock_memgraph(**kwargs: Any) -> Any:
    from cvcdocdb import neo4j_graph
    from cvcdocdb.memgraph_graph import MemgraphGraph

    sent: List[str] = []

    def run(query: str, *args: Any, **kw: Any) -> Any:
        sent.append(query)
        return mock.MagicMock()

    with mock.patch.object(neo4j_graph.GraphDatabase, "driver") as driver:
        driver.return_value.get_server_info.return_value.protocol_version = (5, 2)
        driver.return_value.session.return_value.run.side_effect = run
        graph = MemgraphGraph("bolt://example:7687", "u", "p", **kwargs)
    return graph, sent


class MemgraphUnitTest(unittest.TestCase):
    def test_is_a_neo4j_graph_and_exported_lazily(self) -> None:
        import cvcdocdb
        from cvcdocdb.memgraph_graph import MemgraphGraph
        from cvcdocdb.neo4j_graph import Neo4jGraph

        self.assertIs(cvcdocdb.MemgraphGraph, MemgraphGraph)
        self.assertTrue(issubclass(MemgraphGraph, Neo4jGraph))

    def test_edition_argument_is_not_accepted(self) -> None:
        with self.assertRaises(TypeError):
            _mock_memgraph(edition="enterprise")

    def test_neo4j_enterprise_features_are_unavailable(self) -> None:
        graph, sent = _mock_memgraph()
        self.assertEqual(graph.edition, "community")
        calls = [
            ("create_node_key_constraint", ("User", ["email"])),
            ("create_property_existence_constraint", ("User", "email")),
            ("create_property_type_constraint", ("User", "email", "STRING")),
            ("create_database", ("projecte1",)),
            ("drop_database", ("projecte1",)),
        ]
        for method, args in calls:
            with self.subTest(method=method):
                with self.assertRaises(EnterpriseFeatureError) as caught:
                    getattr(graph, method)(*args)
                self.assertIn("Memgraph", str(caught.exception))
        self.assertEqual(sent, [])

    def test_pk_index_statement_uses_memgraph_syntax(self) -> None:
        from cvcdocdb.memgraph_graph import _memgraph_pk_index_statement

        name, cypher = _memgraph_pk_index_statement("Seccio", ("doc_id", "ordre"))
        self.assertEqual(name, "cvcdocdb_pk_Seccio_doc_id_ordre")
        self.assertEqual(cypher, "CREATE INDEX ON :`Seccio`(`doc_id`, `ordre`)")

    def test_pk_index_statement_rejects_unsafe_identifiers(self) -> None:
        from cvcdocdb.memgraph_graph import _memgraph_pk_index_statement

        with self.assertRaises(ValueError):
            _memgraph_pk_index_statement("Seccio`) DETACH DELETE n //", ("doc_id",))


# ---------------------------------------------------------------------------
# Contracte GraphStore (els mateixos tests que Neo4jGraph)
# ---------------------------------------------------------------------------


class TestMemgraphGraphContract(_contract.TestNeo4jGraph):
    """Tots els tests de contracte de `TestNeo4jGraph`, sobre Memgraph."""

    @classmethod
    def setUpClass(cls) -> None:
        config = _memgraph_config()
        if config is None:
            cls._has_db = False
            cls._graph = None
            return
        from cvcdocdb.memgraph_graph import MemgraphGraph

        cls._has_db = True
        cls._graph = MemgraphGraph(**config)
        cls._graph.query("MATCH (n) DETACH DELETE n")

    def _make_graph(self) -> Any:
        if self._has_db:
            return self._graph
        self.skipTest("Sense MEMGRAPH_URL: cal un Memgraph real")


# ---------------------------------------------------------------------------
# Política de propagació: escenaris idèntics a Neo4j i Memgraph
# ---------------------------------------------------------------------------

@pytest.mark.slow
class MemgraphPropagationPolicyTest(unittest.TestCase):
    """Comportament esperat a Memgraph (no depèn de tenir Neo4j)."""

    def run_on_memgraph(self, scenario: Callable[[Any], List[Outcome]]) -> Dict[str, Any]:
        return run_scenario(_fresh_memgraph, scenario)

    @staticmethod
    def labels(result: Dict[str, Any]) -> List[str]:
        return sorted(label for labels, _ in result["nodes"] for label in labels)

    @staticmethod
    def edge_types(result: Dict[str, Any]) -> List[str]:
        return sorted(edge[1] for edge in result["edges"])

    def test_weak_chain_inserts_its_parents(self) -> None:
        result = self.run_on_memgraph(ps.scenario_weak_chain_inserts_its_parents)
        self.assertEqual(result["outcomes"], [("ok", "")])
        self.assertEqual(self.labels(result), ["Document", "Page", "Section"])
        self.assertEqual(self.edge_types(result), ["HAS_PAGE", "HAS_SECTION"])
        for edge in result["edges"]:
            self.assertIn(("_propagate", "True"), edge[3])

    def test_weak_node_without_parent_is_refused(self) -> None:
        result = self.run_on_memgraph(ps.scenario_weak_node_without_parent_is_refused)
        self.assertEqual(result["outcomes"], [("Exception", "CVCDocDB Exception")])
        self.assertEqual(result["nodes"], [])

    def test_child_keys_must_match_parent(self) -> None:
        result = self.run_on_memgraph(ps.scenario_child_keys_must_match_parent)
        self.assertEqual(result["outcomes"][0][0], "RuntimeError")
        self.assertEqual(self.labels(result), ["Document"])

    def test_duplicate_key_is_refused(self) -> None:
        result = self.run_on_memgraph(ps.scenario_duplicate_key_is_refused)
        self.assertEqual(result["outcomes"], [("RuntimeError", "Duplicate key")])
        self.assertEqual(len(result["nodes"]), 1)

    def test_update_merges_attributes(self) -> None:
        result = self.run_on_memgraph(ps.scenario_update_merges_attributes)
        self.assertEqual(result["outcomes"], [("ok", ""), ("ok", "")])
        props = {dict(p)["doc"]: dict(p) for _, p in result["nodes"]}
        self.assertEqual(props["'D1'"], {"doc": "'D1'", "title": "'B'", "lang": "'ca'", "year": "1900"})
        self.assertIn("'D2'", props)

    def test_update_of_weak_node_keeps_one_parent_edge(self) -> None:
        result = self.run_on_memgraph(ps.scenario_update_of_weak_node_keeps_one_parent_edge)
        self.assertEqual(self.edge_types(result), ["HAS_PAGE", "HAS_SECTION"])
        self.assertEqual(self.labels(result), ["Document", "Page", "Section"])

    def test_replace_deletes_weak_children(self) -> None:
        result = self.run_on_memgraph(ps.scenario_replace_deletes_weak_children)
        self.assertEqual(result["outcomes"], [("ok", "")])
        self.assertEqual(self.labels(result), ["Document"])
        self.assertEqual(result["edges"], [])

    def test_dependencies_become_valor_nodes(self) -> None:
        result = self.run_on_memgraph(ps.scenario_dependencies_become_valor_nodes)
        self.assertEqual(result["outcomes"], [("ok", "")])
        self.assertEqual(self.labels(result).count("Valor"), 3)
        self.assertEqual(len(result["edges"]), 3)

    def test_restrict_refuses_node_with_weak_children(self) -> None:
        result = self.run_on_memgraph(ps.scenario_restrict_refuses_node_with_weak_children)
        self.assertEqual(result["outcomes"], [("RuntimeError", "ON DELETE RESTRICT")])
        self.assertEqual(len(result["nodes"]), 3)

    def test_restrict_refuses_node_with_edges(self) -> None:
        result = self.run_on_memgraph(ps.scenario_restrict_refuses_node_with_edges)
        self.assertEqual(result["outcomes"], [("RuntimeError", "ON DELETE RESTRICT")] * 2)
        self.assertEqual(len(result["edges"]), 1)

    def test_restrict_deletes_isolated_node(self) -> None:
        result = self.run_on_memgraph(ps.scenario_restrict_deletes_isolated_node)
        self.assertEqual(result["outcomes"], [("ok", "")])
        self.assertEqual(len(result["nodes"]), 1)

    def test_propagation_deletes_weak_descendants(self) -> None:
        result = self.run_on_memgraph(ps.scenario_propagation_deletes_weak_descendants)
        self.assertEqual(result["outcomes"], [("ok", "")])
        self.assertEqual(len(result["nodes"]), 1)
        self.assertIn(("doc", "'D9'"), result["nodes"][0][1])
        self.assertEqual(result["edges"], [])

    def test_detach_cascade_keeps_neighbours(self) -> None:
        result = self.run_on_memgraph(ps.scenario_detach_cascade_keeps_neighbours)
        self.assertEqual(result["outcomes"], [("ok", "")])
        self.assertEqual(len(result["nodes"]), 2)
        self.assertEqual(result["edges"], [])

    def test_set_null_keeps_neighbours(self) -> None:
        result = self.run_on_memgraph(ps.scenario_set_null_keeps_neighbours)
        self.assertEqual(result["outcomes"], [("ok", "")])
        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(result["edges"], [])

    def test_relation_fk_violation(self) -> None:
        result = self.run_on_memgraph(ps.scenario_relation_fk_violation)
        self.assertEqual(result["outcomes"], [("RuntimeError", "FK violation")] * 2)
        self.assertEqual(result["edges"], [])

    def test_relation_update_and_replace(self) -> None:
        result = self.run_on_memgraph(ps.scenario_relation_update_and_replace)
        self.assertEqual(result["outcomes"], [("ok", "")] * 3)
        self.assertEqual(len(result["edges"]), 1)
        self.assertEqual(dict(result["edges"][0][3]), {"weight": "3"})

    def test_failed_insert_inside_batch_rolls_back(self) -> None:
        result = self.run_on_memgraph(ps.scenario_failed_insert_inside_batch_rolls_back)
        self.assertEqual(result["outcomes"][0][0], "Exception")
        self.assertEqual(result["nodes"], [])

    def test_create_group_then_restricted_delete(self) -> None:
        result = self.run_on_memgraph(ps.scenario_create_group_then_propagated_delete)
        self.assertEqual(result["outcomes"], [("ok", ""), ("RuntimeError", "ON DELETE RESTRICT")])
        self.assertEqual(self.labels(result), ["Document", "Section", "Section"])
        doc_props = dict(next(p for labels, p in result["nodes"] if labels == ("Document",)))
        self.assertEqual(doc_props.get("_weak_init_done"), "True")

    def test_init_propagation_marks_weak_edges(self) -> None:
        result = self.run_on_memgraph(ps.scenario_init_propagation_marks_weak_edges)
        self.assertEqual(result["outcomes"], [("ok", "")])
        for edge in result["edges"]:
            self.assertIn(("_propagate", "True"), edge[3])


@pytest.mark.slow
class MemgraphMatchesNeo4jTest(unittest.TestCase):
    """Cada escenari deixa el mateix graf (i els mateixos errors) a Neo4j i
    a Memgraph: la política de propagació és idèntica."""

    def test_every_scenario_matches_neo4j(self) -> None:
        if _neo4j_config() is None:
            self.skipTest("Sense NEO4J_DEV_URL: no hi ha Neo4j per comparar")
        if _memgraph_config() is None:
            self.skipTest("Sense MEMGRAPH_URL: cal un Memgraph real")
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario.__name__):
                expected = run_scenario(_fresh_neo4j, scenario)
                actual = run_scenario(_fresh_memgraph, scenario)
                self.assertEqual(actual, expected)


@pytest.mark.slow
class MemgraphSpecificsTest(unittest.TestCase):
    """Operacions on Memgraph necessita Cypher propi."""

    def setUp(self) -> None:
        self.graph = _fresh_memgraph()
        for row in self.graph.query("SHOW INDEX INFO"):
            props = row["property"] if isinstance(row["property"], list) else [row["property"]]
            if row["label"] and props and props != [None]:
                columns = ", ".join(f"`{p}`" for p in props)
                self.graph.query(f"DROP INDEX ON :`{row['label']}`({columns})")

    def tearDown(self) -> None:
        self.graph.query("MATCH (n) DETACH DELETE n")
        self.graph.close()

    def _index_keys(self) -> set:
        keys = set()
        for row in self.graph.query("SHOW INDEX INFO"):
            props = row["property"] if isinstance(row["property"], list) else [row["property"]]
            keys.add((row["label"], tuple(props)))
        return keys

    def test_ensure_pk_indexes_creates_one_index_per_shape_and_is_idempotent(self) -> None:
        self.graph.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        self.graph.insertNode(Node(pk={"doc_id": "D", "ordre": 1}, main_label="Seccio"))
        created = self.graph.ensure_pk_indexes()
        self.assertEqual(sorted(created), ["cvcdocdb_pk_Seccio_doc_id_ordre", "cvcdocdb_pk_User_email"])
        self.assertIn(("User", ("email",)), self._index_keys())
        self.assertIn(("Seccio", ("doc_id", "ordre")), self._index_keys())
        self.assertEqual(self.graph.ensure_pk_indexes(), [])

    def test_auto_pk_indexes(self) -> None:
        from cvcdocdb.memgraph_graph import MemgraphGraph

        graph = MemgraphGraph(**_memgraph_config(), auto_pk_indexes=True)
        try:
            graph.insertNode(Node(pk={"code": "X"}, main_label="Thing"))
            self.assertIn(("Thing", ("code",)), self._index_keys())
        finally:
            graph.close()

    def test_ensure_pk_indexes_refused_inside_batch(self) -> None:
        with self.assertRaises(RuntimeError):
            with self.graph.batch():
                self.graph.ensure_pk_indexes([("User", ("email",))])

    def test_schema_yaml_lists_labels_and_relationship_types(self) -> None:
        _, _, page = document_tree()
        self.graph.insertNode(page)
        yaml_text = self.graph.schema_yaml("proves")
        for name in ("Document", "Section", "Page", "HAS_SECTION", "HAS_PAGE"):
            self.assertIn(name, yaml_text)

    def test_server_edition(self) -> None:
        self.assertIn(self.graph.server_edition(), ("community", "enterprise"))

    def test_migrate_from_networkx_preserves_graph_and_creates_pk_indexes(self) -> None:
        import tempfile

        from cvcdocdb.migration import migrate
        from cvcdocdb.networkx_graph import NetworkXGraph

        source = NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "g.pkl"))
        for node in document_tree():
            source.insertNode(node, insert_parent=False)
        stats = migrate(source, self.graph, create_indexes=True)
        self.assertEqual(stats.nodes_migrated, 3)
        self.assertEqual(stats.edges_migrated, 2)
        self.assertIn(("Page", ("doc", "page", "sec")), self._index_keys())
        graph_state = snapshot(self.graph)
        self.assertEqual(sorted(e[1] for e in graph_state["edges"]), ["HAS_PAGE", "HAS_SECTION"])
