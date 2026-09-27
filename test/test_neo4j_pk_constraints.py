"""Restriccions de clau primària a Neo4j (canvi TRENCADOR — `major_release`).

Fins ara (1.x) una mateixa etiqueta es podia fer servir amb diverses formes
de pk i la unicitat només la garantia cvcdocdb en Python. A partir de la
versió major:

- **Una sola forma de pk per etiqueta**: inserir un node amb una forma de
  pk diferent de la que ja té l'etiqueta falla (`ValueError`).
- **Restricció a la base de dades** per etiqueta (`NODE KEY` a Enterprise,
  `IS UNIQUE` a Community): Neo4j mateix garanteix la unicitat. Substitueix
  l'índex de pk de la mateixa forma (la restricció ja en porta un).
- Actiu per defecte (`pk_constraints=True`); `pk_constraints=False` torna al
  comportament 1.x. Requereix `CONSTRAINT MANAGEMENT`.

Tests `slow`: necessiten un Neo4j real (NEO4J_DEV_URL/USER/PASSWORD/DATABASE).
"""

from __future__ import annotations

import os
import tempfile
import unittest
import warnings

import pytest

from cvcdocdb.base import Node, Relation
from cvcdocdb.migration import MigrationStats, migrate
from cvcdocdb.networkx_graph import NetworkXGraph


def _neo4j_config():
    url = os.environ.get("NEO4J_DEV_URL")
    if not url:
        return None
    return {
        "url": url,
        "user": os.environ.get("NEO4J_DEV_USER", "neo4j"),
        "password": os.environ.get("NEO4J_DEV_PASSWORD", ""),
        "database": os.environ.get("NEO4J_DEV_DATABASE") or None,
    }


class PkConstraintStatementTest(unittest.TestCase):
    def test_node_key_statement(self) -> None:
        from cvcdocdb.neo4j_graph import _pk_constraint_statement

        name, cypher = _pk_constraint_statement("User", ("email",), node_key=True)
        self.assertEqual(name, "cvcdocdb_pkc_User")
        self.assertEqual(
            cypher,
            "CREATE CONSTRAINT `cvcdocdb_pkc_User` IF NOT EXISTS FOR (n:`User`) REQUIRE (n.`email`) IS NODE KEY",
        )

    def test_unique_statement_for_community(self) -> None:
        from cvcdocdb.neo4j_graph import _pk_constraint_statement

        _, cypher = _pk_constraint_statement("Seccio", ("doc_id", "ordre"), node_key=False)
        self.assertTrue(cypher.endswith("REQUIRE (n.`doc_id`, n.`ordre`) IS UNIQUE"))

    def test_unsafe_label_is_rejected(self) -> None:
        from cvcdocdb.neo4j_graph import _pk_constraint_statement

        with self.assertRaises(ValueError):
            _pk_constraint_statement("User`) DETACH DELETE n //", ("email",), node_key=True)


class NonNeo4jDefaultsTest(unittest.TestCase):
    def test_networkx_ensure_pk_constraints_is_a_no_op(self) -> None:
        graph = NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "g.pkl"))
        self.assertEqual(graph.ensure_pk_constraints([("Thing", ("id",))]), [])
        graph.close()

    def test_migration_stats_has_pk_constraints_counter(self) -> None:
        self.assertEqual(MigrationStats().pk_constraints_created, 0)


@pytest.mark.slow
class Neo4jPkConstraintsTest(unittest.TestCase):
    def setUp(self) -> None:
        config = _neo4j_config()
        if config is None:
            self.skipTest("Sense NEO4J_DEV_URL: cal un Neo4j real")
        from cvcdocdb.neo4j_graph import Neo4jGraph

        self.Neo4jGraph = Neo4jGraph
        self.config = config
        self.graph = self._open()
        self._wipe()

    def tearDown(self) -> None:
        if hasattr(self, "graph"):
            self._wipe()
            self.graph.close()

    def _open(self, **kwargs):
        c = self.config
        return self.Neo4jGraph(c["url"], c["user"], c["password"], database=c["database"], **kwargs)

    def _wipe(self) -> None:
        self.graph.query("MATCH (n) DETACH DELETE n")
        for row in self.graph.query("SHOW CONSTRAINTS YIELD name WHERE name STARTS WITH 'cvcdocdb_pk' RETURN name"):
            self.graph.query(f"DROP CONSTRAINT `{row['name']}` IF EXISTS")
        for row in self.graph.query("SHOW INDEXES YIELD name, owningConstraint WHERE name STARTS WITH 'cvcdocdb_pk' "
                                    "AND owningConstraint IS NULL RETURN name"):
            self.graph.query(f"DROP INDEX `{row['name']}` IF EXISTS")
        self.graph._known_pk_shapes_by_label = None  # tornar a llegir l'esquema

    def _constraints(self) -> dict:
        rows = self.graph.query(
            "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties WHERE name STARTS WITH 'cvcdocdb_pkc_' "
            "RETURN name, type, labelsOrTypes, properties"
        )
        return {r["name"]: (r["type"], r["labelsOrTypes"][0], tuple(r["properties"])) for r in rows}

    def test_constraint_created_after_first_commit_by_default(self) -> None:
        self.graph.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        kind, label, props = self._constraints()["cvcdocdb_pkc_User"]
        self.assertIn(kind, ("NODE_KEY", "UNIQUENESS"))
        self.assertEqual((label, props), ("User", ("email",)))

    def test_constraint_created_when_batch_commits(self) -> None:
        with self.graph.batch():
            self.graph.insertNode(Node(pk={"doc_id": "d1"}, main_label="Document"))
            self.assertEqual(self._constraints(), {})
        self.assertIn("cvcdocdb_pkc_Document", self._constraints())

    def test_second_pk_shape_for_a_label_is_rejected(self) -> None:
        """El canvi trencador: una sola forma de pk per etiqueta."""
        self.graph.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        with self.assertRaises(ValueError) as ctx:
            self.graph.insertNode(Node(pk={"niu": "1234567"}, main_label="User"))
        self.assertIn("email", str(ctx.exception))
        self.assertIn("niu", str(ctx.exception))
        self.assertEqual(len(self.graph.query({"main_label": "User"})), 1)

    def test_shape_rule_holds_across_instances(self) -> None:
        """La forma de pk d'una etiqueta es llegeix de les restriccions de la
        base, no només del que ha vist aquesta instància."""
        self.graph.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        other = self._open()
        try:
            with self.assertRaises(ValueError):
                other.insertNode(Node(pk={"niu": "1234567"}, main_label="User"))
        finally:
            other.close()

    def test_database_enforces_uniqueness(self) -> None:
        """Encara que algú se salti cvcdocdb, Neo4j rebutja el duplicat."""
        self.graph.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        with self.assertRaises(Exception):
            self.graph.query("CREATE (:User {email: 'a@uab.cat'})")

    def test_constraint_replaces_pk_index_of_same_shape(self) -> None:
        legacy = self._open(pk_constraints=False)
        try:
            legacy.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
            self.assertEqual(legacy.ensure_pk_indexes(), ["cvcdocdb_pk_User_email"])
        finally:
            legacy.close()
        self.assertEqual(self.graph.ensure_pk_constraints([("User", ["email"])]), ["cvcdocdb_pkc_User"])
        standalone = self.graph.query(
            "SHOW INDEXES YIELD name, owningConstraint WHERE name = 'cvcdocdb_pk_User_email' RETURN name"
        )
        self.assertEqual(standalone, [])

    def test_pk_constraints_false_restores_1x_behaviour(self) -> None:
        legacy = self._open(pk_constraints=False)
        try:
            legacy.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
            legacy.insertNode(Node(pk={"niu": "1234567"}, main_label="User"))  # 1.x: permès
            self.assertEqual(self._constraints(), {})
        finally:
            legacy.close()

    def test_existing_conflicting_shapes_block_the_constraint_clearly(self) -> None:
        legacy = self._open(pk_constraints=False)
        try:
            legacy.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
            legacy.insertNode(Node(pk={"niu": "1234567"}, main_label="User"))
        finally:
            legacy.close()
        with self.assertRaises(ValueError) as ctx:
            self.graph.ensure_pk_constraints([("User", ["email"])])
        self.assertIn("User", str(ctx.exception))

    def test_idempotent(self) -> None:
        self.assertEqual(self.graph.ensure_pk_constraints([("User", ["email"])]), ["cvcdocdb_pkc_User"])
        self.assertEqual(self.graph.ensure_pk_constraints([("User", ["email"])]), [])

    def test_not_allowed_inside_a_batch(self) -> None:
        with self.graph.batch():
            with self.assertRaises(RuntimeError):
                self.graph.ensure_pk_constraints([("User", ["email"])])

    def test_missing_privilege_only_warns_and_keeps_the_write(self) -> None:
        def _forbidden(*args, **kwargs):
            raise PermissionError("no CONSTRAINT MANAGEMENT")

        self.graph.ensure_pk_constraints = _forbidden  # type: ignore[method-assign]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.graph.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        self.assertTrue(any("constraint" in str(w.message).lower() for w in caught))
        self.assertEqual(len(self.graph.query({"main_label": "User"})), 1)

    def test_migrate_creates_constraints(self) -> None:
        source = NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "s.pkl"))
        a = Node(pk={"email": "a@uab.cat"}, main_label="User")
        d = Node(pk={"doc_id": "d1"}, main_label="Document")
        source.insertNode(a)
        source.insertNode(d)
        source.insertRelation(Relation(a, d, "OWNS"))
        target = self._open(pk_constraints=False)  # la migració ho demana explícitament
        try:
            stats = migrate(source, target, create_constraints=True)
        finally:
            source.close()
            target.close()
        self.assertEqual((stats.nodes_migrated, stats.edges_migrated, stats.errors), (2, 1, []))
        self.assertEqual(stats.pk_constraints_created, 2)
        self.assertEqual(set(self._constraints()), {"cvcdocdb_pkc_User", "cvcdocdb_pkc_Document"})
