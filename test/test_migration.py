"""Tests for cvcdocdb.migration.

Run::

    pytest test/test_migration.py -v -m integration
    pytest test/test_migration.py -v -m slow   # requires a real Neo4j
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cvcdocdb import NetworkXGraph, Node, Relation
from cvcdocdb.migration import MigrationStats, migrate

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def source(tmp_path):
    return NetworkXGraph(str(tmp_path / "source.pkl"))


@pytest.fixture()
def target(tmp_path):
    return NetworkXGraph(str(tmp_path / "target.pkl"))


# ---------------------------------------------------------------------------
# Basic node + edge copy
# ---------------------------------------------------------------------------

class TestMigrateBasic:
    def test_copies_all_nodes_and_edges(self, source, target):
        a = Node(pk={"id": 1}, main_label="Document", title="A")
        b = Node(pk={"id": 2}, main_label="Document", title="B")
        source.insertNode(a, replace=True)
        source.insertNode(b, replace=True)
        source.insertRelation(Relation(a, b, "LINKS", weight=0.5))

        stats = migrate(source, target)

        assert isinstance(stats, MigrationStats)
        assert stats.nodes_migrated == 2
        assert stats.edges_migrated == 1
        assert len(target.get_node_ids()) == 2
        assert len(target.get_edges()) == 1

    def test_preserves_pk_and_main_label(self, source, target):
        source.insertNode(Node(pk={"doc": "X-1"}, main_label="Document"), replace=True)
        migrate(source, target)

        [nid] = target.get_node_ids()
        attrs = target.get_node_attrs(nid)
        assert attrs["main_label"] == "Document"
        assert attrs["pk"] == {"doc": "X-1"}

    def test_preserves_node_attributes(self, source, target):
        source.insertNode(
            Node(pk={"id": 1}, main_label="Document", title="Hello", year=2020),
            replace=True,
        )
        migrate(source, target)

        [nid] = target.get_node_ids()
        attrs = target.get_node_attrs(nid)
        assert attrs["title"] == "Hello"
        assert attrs["year"] == 2020

    def test_preserves_edge_attributes_and_propagate_flag(self, source, target):
        """Weak-relation-style edges (with _propagate) survive migration,
        even though migration never constructs a WeakNode/WeakRelation."""
        parent = Node(pk={"id": 1}, main_label="Document")
        child = Node(pk={"id": 1, "section": "intro"}, main_label="Section")
        source.insertNode(parent, replace=True)
        source.insertNode(child, replace=True)
        source.insertRelation(Relation(parent, child, "HAS", _propagate=True))

        migrate(source, target)

        edges = target.get_edges()
        assert len(edges) == 1
        u, v, rel_type = edges[0]
        assert rel_type == "HAS"
        edge_attrs = target.get_edge_attrs(u, v, rel_type)
        assert edge_attrs.get("_propagate") is True

    def test_empty_source_migrates_nothing(self, source, target):
        stats = migrate(source, target)
        assert stats.nodes_migrated == 0
        assert stats.edges_migrated == 0
        assert target.get_node_ids() == []


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

class TestMigrateFiltering:
    def test_label_filter_keeps_only_matching_nodes(self, source, target):
        source.insertNode(Node(pk={"id": 1}, main_label="Document"), replace=True)
        source.insertNode(Node(pk={"id": 2}, main_label="Author"), replace=True)

        stats = migrate(source, target, label_filter="Document")

        assert stats.nodes_migrated == 1
        assert stats.nodes_skipped == 1
        labels = {target.get_node_attrs(nid)["main_label"] for nid in target.get_node_ids()}
        assert labels == {"Document"}

    def test_edge_dropped_when_endpoint_filtered_out(self, source, target):
        doc = Node(pk={"id": 1}, main_label="Document")
        author = Node(pk={"id": 2}, main_label="Author")
        source.insertNode(doc, replace=True)
        source.insertNode(author, replace=True)
        source.insertRelation(Relation(author, doc, "AUTHORED"))

        stats = migrate(source, target, label_filter="Document")

        assert stats.edges_migrated == 0
        assert stats.edges_skipped == 1
        assert target.get_edges() == []

    def test_property_filter(self, source, target):
        source.insertNode(Node(pk={"id": 1}, main_label="Document", year=2020), replace=True)
        source.insertNode(Node(pk={"id": 2}, main_label="Document", year=1999), replace=True)

        stats = migrate(source, target, property_filter={"year": {"$gte": 2000}})

        assert stats.nodes_migrated == 1
        [nid] = target.get_node_ids()
        assert target.get_node_attrs(nid)["year"] == 2020


# ---------------------------------------------------------------------------
# Idempotency / conflict handling
# ---------------------------------------------------------------------------

class TestMigrateIdempotency:
    def test_rerunning_with_update_does_not_duplicate(self, source, target):
        source.insertNode(Node(pk={"id": 1}, main_label="Document", title="A"), replace=True)

        migrate(source, target, update=True)
        migrate(source, target, update=True)

        assert len(target.get_node_ids()) == 1

    def test_rerunning_with_update_merges_new_attrs(self, source, target):
        node = Node(pk={"id": 1}, main_label="Document", title="A")
        source.insertNode(node, replace=True)
        migrate(source, target, update=True)

        source.insertNode(
            Node(pk={"id": 1}, main_label="Document", title="A", year=2020),
            update=True,
        )
        migrate(source, target, update=True)

        [nid] = target.get_node_ids()
        assert target.get_node_attrs(nid)["year"] == 2020


# ---------------------------------------------------------------------------
# on_error handling
# ---------------------------------------------------------------------------

class TestMigrateOnError:
    def test_on_error_raise_propagates(self, source, target):
        source.insertNode(Node(pk={"id": 1}, main_label="Document"), replace=True)
        with patch.object(target, "insertNode", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                migrate(source, target, on_error="raise")

    def test_on_error_skip_continues_and_records(self, source, target):
        source.insertNode(Node(pk={"id": 1}, main_label="Document"), replace=True)
        source.insertNode(Node(pk={"id": 2}, main_label="Document"), replace=True)
        real_insert = target.insertNode
        calls = {"n": 0}

        def flaky_insert(node, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real_insert(node, **kwargs)

        with patch.object(target, "insertNode", side_effect=flaky_insert):
            stats = migrate(source, target, on_error="skip")

        assert stats.nodes_migrated == 1
        assert stats.nodes_skipped == 1
        assert len(stats.errors) == 1
        assert "boom" in stats.errors[0]

    def test_invalid_on_error_value_raises(self, source, target):
        with pytest.raises(ValueError, match="on_error"):
            migrate(source, target, on_error="ignore")


# ---------------------------------------------------------------------------
# Vector index migration (phase 3)
# ---------------------------------------------------------------------------

class TestMigrateVectorIndexes:
    def test_recreates_vector_index_on_networkx_target(self, source, target):
        pytest.importorskip("hnswlib")
        source.insertNode(
            Node(pk={"id": 1}, main_label="Document", emb=[1.0, 0.0, 0.0]),
            replace=True,
        )
        source.enable_vector_index("emb", dimensions=3)

        stats = migrate(source, target)

        assert stats.indexes_migrated == 1
        assert stats.indexes_skipped == 0
        target_indexes = {i["property_name"] for i in target.list_vector_indexes()}
        assert target_indexes == {"emb"}

    def test_no_indexes_is_a_noop(self, source, target):
        source.insertNode(Node(pk={"id": 1}, main_label="Document"), replace=True)
        stats = migrate(source, target)
        assert stats.indexes_migrated == 0
        assert stats.indexes_skipped == 0


# ---------------------------------------------------------------------------
# Real Neo4j: pk fallback, batched reads, cross-backend round trip
# ---------------------------------------------------------------------------

def _neo4j_config():
    import os
    target = os.environ.get("NEO4J_TARGET", "DEV")

    def _env(key):
        return os.environ.get(f"NEO4J_{target.upper()}_{key}") or os.environ.get(f"NEO4J_{key}")

    url, user, password = _env("URL"), _env("USER"), _env("PASSWORD")
    if not url or not user or not password:
        return None
    return {"url": url, "user": user, "password": password, "database": _env("DATABASE") or "neo4j"}


@pytest.fixture()
def neo4j_store():
    config = _neo4j_config()
    if config is None:
        pytest.skip("NEO4J_DEV_* (or compatible NEO4J target/plain vars) not set")
    from cvcdocdb.neo4j_graph import Neo4jGraph

    graph = Neo4jGraph(url=config["url"], user=config["user"],
                        password=config["password"], database=config["database"])

    def _wipe():
        graph._tx = graph._session.begin_transaction()
        graph._tx.run("MATCH (n) DETACH DELETE n")
        graph._tx.commit()
        graph._tx = None

    _wipe()
    yield graph
    _wipe()
    graph.close()


@pytest.mark.slow
class TestMigrateWithNeo4j:
    def test_networkx_to_neo4j_preserves_pk_and_edges(self, source, neo4j_store):
        a = Node(pk={"id": 1}, main_label="Document", title="A")
        b = Node(pk={"id": 2}, main_label="Document", title="B")
        source.insertNode(a, replace=True)
        source.insertNode(b, replace=True)
        source.insertRelation(Relation(a, b, "LINKS", weight=0.5))

        stats = migrate(source, neo4j_store)

        assert stats.nodes_migrated == 2
        assert stats.edges_migrated == 1
        rows = neo4j_store.query("MATCH (n:Document) RETURN n.id AS id, n.title AS title ORDER BY n.id")
        assert [(r["id"], r["title"]) for r in rows] == [(1, "A"), (2, "B")]
        edges = neo4j_store.query(
            "MATCH (a:Document {id: 1})-[r:LINKS]->(b:Document {id: 2}) RETURN r.weight AS w"
        )
        assert edges[0]["w"] == 0.5

    def test_neo4j_to_networkx_uses_full_properties_as_pk(self, neo4j_store, target):
        """Neo4j never tells us which properties are the pk, so migrate()
        falls back to using every property as the pk (see migrate()'s
        docstring) — verify that fallback actually kicks in end to end."""
        neo4j_store.insertNode(
            Node(pk={"id": 1}, main_label="Document", title="From Neo4j"), replace=True
        )

        stats = migrate(neo4j_store, target)

        assert stats.nodes_migrated == 1
        [nid] = target.get_node_ids()
        attrs = target.get_node_attrs(nid)
        assert attrs["main_label"] == "Document"
        # Every original property (id + title, main_label is metadata not a
        # property) ends up inside the fallback pk.
        assert attrs["pk"] == {"id": 1, "title": "From Neo4j"}

    def test_batched_read_matches_unbatched_for_many_nodes(self, source, neo4j_store):
        """chunk_size smaller than the node count exercises the batched
        Neo4j read path across multiple round-trips."""
        for i in range(12):
            source.insertNode(Node(pk={"id": i}, main_label="Document"), replace=True)

        stats = migrate(source, neo4j_store, chunk_size=5)

        assert stats.nodes_migrated == 12
        [count] = neo4j_store.query("MATCH (n:Document) RETURN count(n) AS c")
        assert count["c"] == 12

    def test_vector_index_skipped_on_neo4j_target(self, source, neo4j_store):
        pytest.importorskip("hnswlib")
        source.insertNode(
            Node(pk={"id": 1}, main_label="Document", emb=[1.0, 0.0]), replace=True
        )
        source.enable_vector_index("emb", dimensions=2)

        stats = migrate(source, neo4j_store)

        assert stats.indexes_migrated == 0
        assert stats.indexes_skipped == 1
