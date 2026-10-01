"""Mode Enterprise opcional de Neo4jGraph.

Per defecte `Neo4jGraph` treballa en mode Community (`edition="community"`)
i les operacions exclusives de Neo4j Enterprise queden desactivades: llancen
`EnterpriseFeatureError` sense enviar res al servidor. Amb
`edition="enterprise"` s'activen (restriccions NODE KEY, d'existència i de
tipus de propietat, i gestió de bases de dades), sempre que el servidor sigui
realment Enterprise.

Els tests sense servidor fan servir un driver simulat. Els `slow`:
- `NEO4J_DEV_URL` (Community, el de CI): comproven que el mode Enterprise
  es nega a treballar contra un servidor Community.
- `NEO4J_ENTERPRISE_URL` (opcional, cal llicència): proven les funcions
  Enterprise de debò. Es salten si no està definit.
"""

from __future__ import annotations

import os
import unittest
import warnings
from typing import Any, List
from unittest import mock

import pytest

from cvcdocdb import neo4j_graph
from cvcdocdb.neo4j_graph import EnterpriseFeatureError, Neo4jGraph


def _make_graph(edition: Any = None, server_edition: str = "enterprise") -> "tuple[Neo4jGraph, List[str]]":
    """Neo4jGraph amb un driver simulat. Retorna el graf i la llista on
    s'acumulen les consultes Cypher que envia (a qualsevol sessió)."""
    sent: List[str] = []

    def run(query: str, *args: Any, **kwargs: Any) -> Any:
        sent.append(query)
        result = mock.MagicMock()
        if "dbms.components" in query:
            result.single.return_value = {"edition": server_edition}
        return result

    with mock.patch.object(neo4j_graph.GraphDatabase, "driver") as driver:
        driver.return_value.get_server_info.return_value.protocol_version = (5, 0)
        driver.return_value.session.return_value.run.side_effect = run
        kwargs = {} if edition is None else {"edition": edition}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            graph = Neo4jGraph("bolt://example:7687", "u", "p", **kwargs)
    graph._test_driver = driver.return_value  # type: ignore[attr-defined]
    return graph, sent


ENTERPRISE_CALLS = [
    ("create_node_key_constraint", ("User", ["email"])),
    ("create_property_existence_constraint", ("User", "email")),
    ("create_property_type_constraint", ("User", "email", "STRING")),
    ("create_database", ("projecte1",)),
    ("drop_database", ("projecte1",)),
]


class EditionSelectionTest(unittest.TestCase):
    def test_default_edition_is_community(self) -> None:
        graph, _ = _make_graph()
        self.assertEqual(graph.edition, "community")

    def test_enterprise_edition_can_be_selected(self) -> None:
        graph, _ = _make_graph("enterprise")
        self.assertEqual(graph.edition, "enterprise")

    def test_edition_is_case_insensitive(self) -> None:
        graph, _ = _make_graph("Enterprise")
        self.assertEqual(graph.edition, "enterprise")

    def test_unknown_edition_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _make_graph("aura")

    def test_enterprise_mode_warns_about_license_and_testing(self) -> None:
        with mock.patch.object(neo4j_graph.GraphDatabase, "driver") as driver:
            driver.return_value.get_server_info.return_value.protocol_version = (5, 0)
            with self.assertWarns(UserWarning) as caught:
                Neo4jGraph("bolt://example:7687", "u", "p", edition="enterprise")
        message = str(caught.warning)
        self.assertIn("license", message)
        self.assertIn("not fully tested", message)

    def test_community_mode_does_not_warn(self) -> None:
        with mock.patch.object(neo4j_graph.GraphDatabase, "driver") as driver:
            driver.return_value.get_server_info.return_value.protocol_version = (5, 0)
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                Neo4jGraph("bolt://example:7687", "u", "p")

    def test_server_edition_is_read_from_dbms_components(self) -> None:
        graph, sent = _make_graph(server_edition="community")
        self.assertEqual(graph.server_edition(), "community")
        self.assertTrue(any("dbms.components" in q for q in sent))


class CommunityModeDisablesEnterpriseFeaturesTest(unittest.TestCase):
    def test_enterprise_operations_raise_without_contacting_the_server(self) -> None:
        for method, args in ENTERPRISE_CALLS:
            with self.subTest(method=method):
                graph, sent = _make_graph()
                with self.assertRaises(EnterpriseFeatureError) as caught:
                    getattr(graph, method)(*args)
                self.assertIn('edition="enterprise"', str(caught.exception))
                self.assertEqual(sent, [])

    def test_enterprise_feature_error_is_a_runtime_error(self) -> None:
        self.assertTrue(issubclass(EnterpriseFeatureError, RuntimeError))


class EnterpriseModeAgainstCommunityServerTest(unittest.TestCase):
    def test_enterprise_operations_refuse_a_community_server(self) -> None:
        for method, args in ENTERPRISE_CALLS:
            with self.subTest(method=method):
                graph, sent = _make_graph("enterprise", server_edition="community")
                with self.assertRaises(EnterpriseFeatureError) as caught:
                    getattr(graph, method)(*args)
                self.assertIn("community", str(caught.exception))
                self.assertTrue(all("dbms.components" in q for q in sent))


class EnterpriseCypherTest(unittest.TestCase):
    def _run(self, method: str, *args: Any, **kwargs: Any) -> "tuple[Any, str]":
        graph, sent = _make_graph("enterprise")
        result = getattr(graph, method)(*args, **kwargs)
        statements = [q for q in sent if "dbms.components" not in q]
        self.assertEqual(len(statements), 1, statements)
        return result, statements[0]

    def test_node_key_constraint(self) -> None:
        name, cypher = self._run("create_node_key_constraint", "Seccio", ["ordre", "doc_id"])
        self.assertEqual(name, "cvcdocdb_nodekey_Seccio_doc_id_ordre")
        self.assertEqual(
            cypher,
            "CREATE CONSTRAINT `cvcdocdb_nodekey_Seccio_doc_id_ordre` IF NOT EXISTS "
            "FOR (n:`Seccio`) REQUIRE (n.`doc_id`, n.`ordre`) IS NODE KEY",
        )

    def test_node_key_constraint_requires_properties(self) -> None:
        graph, _ = _make_graph("enterprise")
        with self.assertRaises(ValueError):
            graph.create_node_key_constraint("User", [])

    def test_node_existence_constraint(self) -> None:
        name, cypher = self._run("create_property_existence_constraint", "User", "email")
        self.assertEqual(name, "cvcdocdb_exists_User_email")
        self.assertEqual(
            cypher,
            "CREATE CONSTRAINT `cvcdocdb_exists_User_email` IF NOT EXISTS "
            "FOR (n:`User`) REQUIRE n.`email` IS NOT NULL",
        )

    def test_relationship_existence_constraint(self) -> None:
        name, cypher = self._run(
            "create_property_existence_constraint", "HAS_PAGE", "ordre", entity="relationship"
        )
        self.assertEqual(name, "cvcdocdb_exists_HAS_PAGE_ordre")
        self.assertEqual(
            cypher,
            "CREATE CONSTRAINT `cvcdocdb_exists_HAS_PAGE_ordre` IF NOT EXISTS "
            "FOR ()-[r:`HAS_PAGE`]-() REQUIRE r.`ordre` IS NOT NULL",
        )

    def test_property_type_constraint(self) -> None:
        name, cypher = self._run("create_property_type_constraint", "User", "edat", "integer")
        self.assertEqual(name, "cvcdocdb_type_User_edat")
        self.assertEqual(
            cypher,
            "CREATE CONSTRAINT `cvcdocdb_type_User_edat` IF NOT EXISTS "
            "FOR (n:`User`) REQUIRE n.`edat` IS :: INTEGER",
        )

    def test_property_type_constraint_accepts_lists(self) -> None:
        _, cypher = self._run("create_property_type_constraint", "User", "tags", "LIST<STRING NOT NULL>")
        self.assertTrue(cypher.endswith("REQUIRE n.`tags` IS :: LIST<STRING NOT NULL>"))

    def test_property_type_constraint_rejects_unknown_types(self) -> None:
        graph, _ = _make_graph("enterprise")
        for bad in ("TEXT", "STRING; DROP DATABASE neo4j", "LIST<STRING>"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                graph.create_property_type_constraint("User", "email", bad)

    def test_unknown_entity_is_rejected(self) -> None:
        graph, _ = _make_graph("enterprise")
        with self.assertRaises(ValueError):
            graph.create_property_existence_constraint("User", "email", entity="graph")

    def test_unsafe_identifiers_are_rejected(self) -> None:
        graph, _ = _make_graph("enterprise")
        with self.assertRaises(ValueError):
            graph.create_node_key_constraint("User`) DETACH DELETE n //", ["email"])
        with self.assertRaises(ValueError):
            graph.create_property_existence_constraint("User", "email` IS NOT NULL //")

    def test_custom_constraint_name(self) -> None:
        name, cypher = self._run("create_node_key_constraint", "User", ["email"], name="user_key")
        self.assertEqual(name, "user_key")
        self.assertIn("CREATE CONSTRAINT `user_key` IF NOT EXISTS", cypher)

    def test_create_database_runs_on_the_system_database(self) -> None:
        graph, sent = _make_graph("enterprise")
        graph.create_database("projecte-1.dev")
        self.assertIn("CREATE DATABASE `projecte-1.dev` IF NOT EXISTS WAIT", sent)
        graph._test_driver.session.assert_any_call(database="system")

    def test_create_database_without_waiting(self) -> None:
        graph, sent = _make_graph("enterprise")
        graph.create_database("projecte1", wait=False)
        self.assertIn("CREATE DATABASE `projecte1` IF NOT EXISTS", sent)

    def test_drop_database(self) -> None:
        graph, sent = _make_graph("enterprise")
        graph.drop_database("projecte1")
        self.assertIn("DROP DATABASE `projecte1` IF EXISTS WAIT", sent)

    def test_invalid_database_names_are_rejected(self) -> None:
        graph, _ = _make_graph("enterprise")
        for bad in ("ab", "1abc", "db`; DROP DATABASE neo4j", "system", "a" * 64):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                graph.create_database(bad)

    def test_schema_changes_are_refused_inside_a_transaction(self) -> None:
        graph, _ = _make_graph("enterprise")
        graph._tx = mock.MagicMock()
        with self.assertRaises(RuntimeError):
            graph.create_node_key_constraint("User", ["email"])


@pytest.mark.slow
class CommunityServerTest(unittest.TestCase):
    """Contra el Neo4j Community de CI/local."""

    def setUp(self) -> None:
        url = os.environ.get("NEO4J_DEV_URL")
        if not url:
            self.skipTest("Sense NEO4J_DEV_URL: cal un Neo4j real")
        self.config = dict(
            url=url,
            user=os.environ.get("NEO4J_DEV_USER", "neo4j"),
            password=os.environ.get("NEO4J_DEV_PASSWORD", ""),
            database=os.environ.get("NEO4J_DEV_DATABASE") or None,
        )

    def test_enterprise_mode_refuses_a_community_server(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            graph = Neo4jGraph(edition="enterprise", **self.config)
        try:
            if graph.server_edition() != "community":
                self.skipTest("NEO4J_DEV_URL no és un servidor Community")
            with self.assertRaises(EnterpriseFeatureError):
                graph.create_node_key_constraint("User", ["email"])
        finally:
            graph.close()


@pytest.mark.slow
class EnterpriseServerTest(unittest.TestCase):
    """Només amb un Neo4j Enterprise (NEO4J_ENTERPRISE_URL; cal llicència)."""

    LABEL = "CvcdocdbEnterpriseTest"

    def setUp(self) -> None:
        url = os.environ.get("NEO4J_ENTERPRISE_URL")
        if not url:
            self.skipTest("Sense NEO4J_ENTERPRISE_URL: cal un Neo4j Enterprise")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.graph = Neo4jGraph(
                url,
                os.environ.get("NEO4J_ENTERPRISE_USER", "neo4j"),
                os.environ.get("NEO4J_ENTERPRISE_PASSWORD", ""),
                database=os.environ.get("NEO4J_ENTERPRISE_DATABASE") or None,
                edition="enterprise",
            )
        self.created: List[str] = []

    def tearDown(self) -> None:
        if not hasattr(self, "graph"):
            return
        for name in self.created:
            self.graph.drop_constraint(name)
        self.graph.query(f"MATCH (n:{self.LABEL}) DETACH DELETE n")
        self.graph.close()

    def test_server_is_enterprise(self) -> None:
        self.assertEqual(self.graph.server_edition(), "enterprise")

    def test_node_key_constraint_rejects_nodes_without_the_key(self) -> None:
        self.created.append(self.graph.create_node_key_constraint(self.LABEL, ["code"]))
        with self.assertRaises(Exception):
            self.graph.query(f"CREATE (:{self.LABEL} {{name: 'sense codi'}})")

    def test_existence_constraint_is_idempotent(self) -> None:
        first = self.graph.create_property_existence_constraint(self.LABEL, "name")
        self.created.append(first)
        self.assertEqual(self.graph.create_property_existence_constraint(self.LABEL, "name"), first)
