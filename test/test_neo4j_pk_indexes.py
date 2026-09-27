"""Índexs sobre les propietats de la clau primària (Neo4j).

Cada insertNode/lookup fa `MATCH (n:Label) WHERE <pk>`; sense índex, Neo4j
recorre tots els nodes de l'etiqueta i una migració sencera és quadràtica.
Aquests índexs són NO únics (un per etiqueta i forma de pk) i per tant
compatibles amb el disseny actual, on una etiqueta pot tenir diverses formes
de pk — canvi additiu, no trencador. Les restriccions (NODE KEY/UNIQUE) són
un canvi trencador i viuen a `major_release`.

Tests `slow`: necessiten un Neo4j real (NEO4J_DEV_URL/USER/PASSWORD/DATABASE);
es salten si no n'hi ha. La base de dades de proves es buida (dades i
índexs creats per aquests tests) abans de cada test.
"""

from __future__ import annotations

import os
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


class PkIndexStatementTest(unittest.TestCase):
    """Generació del Cypher (sense servidor)."""

    def test_statement_escapes_and_names_the_index(self) -> None:
        from cvcdocdb.neo4j_graph import _pk_index_statement

        name, cypher = _pk_index_statement("User", ("email",))
        self.assertEqual(name, "cvcdocdb_pk_User_email")
        self.assertEqual(
            cypher, "CREATE INDEX `cvcdocdb_pk_User_email` IF NOT EXISTS FOR (n:`User`) ON (n.`email`)"
        )

    def test_composite_pk_uses_all_properties_in_stable_order(self) -> None:
        from cvcdocdb.neo4j_graph import _pk_index_statement

        name, cypher = _pk_index_statement("Seccio", ("doc_id", "ordre"))
        self.assertEqual(name, "cvcdocdb_pk_Seccio_doc_id_ordre")
        self.assertIn("ON (n.`doc_id`, n.`ordre`)", cypher)

    def test_unsafe_label_is_rejected(self) -> None:
        from cvcdocdb.neo4j_graph import _pk_index_statement

        with self.assertRaises(ValueError):
            _pk_index_statement("User`) DETACH DELETE n //", ("email",))

    def test_pk_shape_ignores_internal_id_and_is_sorted(self) -> None:
        from cvcdocdb.neo4j_graph import _pk_shape

        self.assertEqual(_pk_shape("User", {"email": "a", "domain": "b"}), ("User", ("domain", "email")))
        self.assertIsNone(_pk_shape("User", {"neo4j_id": 3}))
        self.assertIsNone(_pk_shape("User", None))


class GraphStoreDefaultTest(unittest.TestCase):
    def test_networkx_ensure_pk_indexes_is_a_no_op(self) -> None:
        """NetworkXGraph ja té el seu propi índex de pk en memòria."""
        import tempfile

        graph = NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "g.pkl"))
        graph.insertNode(Node(pk={"id": "1"}, main_label="Thing"))
        self.assertEqual(graph.ensure_pk_indexes([("Thing", ("id",))]), [])
        graph.close()

    def test_migration_stats_has_pk_indexes_counter(self) -> None:
        self.assertEqual(MigrationStats().pk_indexes_created, 0)

    def test_migrate_with_create_indexes_on_networkx_target(self) -> None:
        import tempfile

        tmp = tempfile.mkdtemp()
        source = NetworkXGraph(persistence_path=os.path.join(tmp, "s.pkl"))
        source.insertNode(Node(pk={"id": "1"}, main_label="Thing"))
        target = NetworkXGraph(persistence_path=os.path.join(tmp, "t.pkl"))
        stats = migrate(source, target, create_indexes=True)
        self.assertEqual((stats.nodes_migrated, stats.pk_indexes_created, stats.errors), (1, 0, []))
        source.close()
        target.close()


@pytest.mark.slow
class Neo4jPkIndexesTest(unittest.TestCase):
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
        for row in self.graph.query("SHOW INDEXES YIELD name WHERE name STARTS WITH 'cvcdocdb_pk_' RETURN name"):
            self.graph.query(f"DROP INDEX `{row['name']}` IF EXISTS")

    def _pk_indexes(self) -> dict:
        rows = self.graph.query(
            "SHOW INDEXES YIELD name, labelsOrTypes, properties WHERE name STARTS WITH 'cvcdocdb_pk_' "
            "RETURN name, labelsOrTypes, properties"
        )
        return {r["name"]: (r["labelsOrTypes"][0], tuple(r["properties"])) for r in rows}

    def test_indexes_for_shapes_seen_by_inserts_and_idempotent(self) -> None:
        self.graph.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        self.graph.insertNode(Node(pk={"doc_id": "d1", "ordre": 1}, main_label="Seccio"))

        created = self.graph.ensure_pk_indexes()
        self.assertEqual(sorted(created), ["cvcdocdb_pk_Seccio_doc_id_ordre", "cvcdocdb_pk_User_email"])
        self.assertEqual(self._pk_indexes()["cvcdocdb_pk_User_email"], ("User", ("email",)))
        self.assertEqual(self.graph.ensure_pk_indexes(), [])  # idempotent

    def test_one_index_per_pk_shape_of_the_same_label(self) -> None:
        """Compatible amb etiquetes que fan servir diverses formes de pk."""
        self.graph.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        self.graph.insertNode(Node(pk={"niu": "1234567"}, main_label="User"))
        self.assertEqual(
            sorted(self.graph.ensure_pk_indexes()), ["cvcdocdb_pk_User_email", "cvcdocdb_pk_User_niu"]
        )

    def test_explicit_shapes(self) -> None:
        self.assertEqual(self.graph.ensure_pk_indexes([("Document", ["doc_id"])]), ["cvcdocdb_pk_Document_doc_id"])

    def test_equivalent_existing_index_is_reused(self) -> None:
        self.graph.query("CREATE INDEX my_own_user_email IF NOT EXISTS FOR (n:User) ON (n.email)")
        try:
            self.assertEqual(self.graph.ensure_pk_indexes([("User", ["email"])]), [])
        finally:
            self.graph.query("DROP INDEX my_own_user_email IF EXISTS")

    def test_not_allowed_inside_a_batch(self) -> None:
        """Neo4j no permet canvis d'esquema en una transacció amb escriptures."""
        with self.graph.batch():
            self.graph.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
            with self.assertRaises(RuntimeError):
                self.graph.ensure_pk_indexes()

    def test_index_is_used_by_pk_lookups(self) -> None:
        self.graph.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        self.graph.ensure_pk_indexes()
        self.graph.query("CALL db.awaitIndexes(60)")
        summary = self.graph._session.run("EXPLAIN MATCH (n:User) WHERE n.email = 'a@uab.cat' RETURN n").consume()
        self.assertIn("NodeIndexSeek", str(summary.plan))

    def test_auto_pk_indexes_after_commit(self) -> None:
        graph = self._open(auto_pk_indexes=True)
        try:
            graph.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))  # tx pròpia
            self.assertIn("cvcdocdb_pk_User_email", self._pk_indexes())
            with graph.batch():
                graph.insertNode(Node(pk={"doc_id": "d1"}, main_label="Document"))
                self.assertNotIn("cvcdocdb_pk_Document_doc_id", self._pk_indexes())
            self.assertIn("cvcdocdb_pk_Document_doc_id", self._pk_indexes())  # en tancar el batch
        finally:
            graph.close()

    def test_auto_pk_indexes_off_by_default(self) -> None:
        self.graph.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        self.assertEqual(self._pk_indexes(), {})

    def test_auto_pk_indexes_failure_only_warns_and_keeps_the_write(self) -> None:
        graph = self._open(auto_pk_indexes=True)
        try:
            def _forbidden(*args, **kwargs):
                raise PermissionError("no INDEX MANAGEMENT")

            graph.ensure_pk_indexes = _forbidden  # type: ignore[method-assign]
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                graph.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
            self.assertTrue(any("index" in str(w.message).lower() for w in caught))
            self.assertEqual(len(graph.query({"main_label": "User"})), 1)
        finally:
            graph.close()

    def test_migrate_creates_pk_indexes_on_neo4j_target(self) -> None:
        import tempfile

        source = NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "s.pkl"))
        a = Node(pk={"email": "a@uab.cat"}, main_label="User")
        d = Node(pk={"doc_id": "d1"}, main_label="Document")
        source.insertNode(a)
        source.insertNode(d)
        source.insertRelation(Relation(a, d, "OWNS"))

        stats = migrate(source, self.graph, create_indexes=True)
        source.close()

        self.assertEqual((stats.nodes_migrated, stats.edges_migrated, stats.errors), (2, 1, []))
        self.assertEqual(stats.pk_indexes_created, 2)
        self.assertEqual(set(self._pk_indexes()), {"cvcdocdb_pk_User_email", "cvcdocdb_pk_Document_doc_id"})
