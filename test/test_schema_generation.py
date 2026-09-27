"""Tests for schema introspection and YAML generation.

Tests both Neo4jGraph (requires credentials) and NetworkXGraph (sample GOT data).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

try:
    import yaml
except ImportError:
    yaml = None

from cvcdocdb.base import Node, Relation, WeakNode
from cvcdocdb.graph_store import GraphStore
from cvcdocdb.networkx_graph import NetworkXGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_got_graph() -> NetworkXGraph:
    """Create a NetworkXGraph with GOT-like sample data."""
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "got.pkl")
    g = NetworkXGraph(persistence_path=path)
    g._temp_path = tmp

    # Characters
    g.insertNode(Node(pk={"name": "Jon Snow"}, main_label="Character",
                      house="Stark", age=23, gender="Male", culture="Northmen"), replace=True)
    g.insertNode(Node(pk={"name": "Daenerys"}, main_label="Character",
                      house="Targaryen", age=20, gender="Female", culture="Valyrian"), replace=True)
    g.insertNode(Node(pk={"name": "Tyrion"}, main_label="Character",
                      house="Lannister", age=30, gender="Male", culture="Westermen"), replace=True)
    g.insertNode(Node(pk={"name": "Arya"}, main_label="Character",
                      house="Stark", age=12, gender="Female", culture="Northmen"), replace=True)

    # Locations
    g.insertNode(Node(pk={"name": "Winterfell"}, main_label="Location",
                      region="North", type="Castle"), replace=True)
    g.insertNode(Node(pk={"name": "King's Landing"}, main_label="Location",
                      region="East", type="City"), replace=True)
    g.insertNode(Node(pk={"name": "Dragonstone"}, main_label="Location",
                      region="East", type="Island"), replace=True)

    # Relations
    g.insertRelation(Relation(
        Node(pk={"name": "Jon Snow"}, main_label="Character"),
        Node(pk={"name": "Winterfell"}, main_label="Location"),
        "LIVES_IN",
    ), update=True)
    g.insertRelation(Relation(
        Node(pk={"name": "Daenerys"}, main_label="Character"),
        Node(pk={"name": "Dragonstone"}, main_label="Location"),
        "LIVES_IN",
    ), update=True)
    g.insertRelation(Relation(
        Node(pk={"name": "Tyrion"}, main_label="Character"),
        Node(pk={"name": "King's Landing"}, main_label="Location"),
        "LIVES_IN",
    ), update=True)
    g.insertRelation(Relation(
        Node(pk={"name": "Jon Snow"}, main_label="Character"),
        Node(pk={"name": "Daenerys"}, main_label="Character"),
        "KNOWS",
        strength="strong",
    ), update=True)
    g.insertRelation(Relation(
        Node(pk={"name": "Tyrion"}, main_label="Character"),
        Node(pk={"name": "Daenerys"}, main_label="Character"),
        "KNOWS",
        strength="weak",
    ), update=True)
    g.insertRelation(Relation(
        Node(pk={"name": "Daenerys"}, main_label="Character"),
        Node(pk={"name": "King's Landing"}, main_label="Location"),
        "RULES",
        since=302,
    ), update=True)

    return g


# ---------------------------------------------------------------------------
# NetworkXGraph — schema introspection
# ---------------------------------------------------------------------------


class NetworkXSchemaTest(unittest.TestCase):
    """Tests for NetworkXGraph.schema_yaml()."""

    def setUp(self) -> None:
        self.graph = _make_got_graph()

    def tearDown(self) -> None:
        path = getattr(self.graph, "_temp_path", None)
        try:
            self.graph.close()
        except Exception:
            pass
        if path and os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)

    def test_schema_yaml_returns_string(self) -> None:
        """schema_yaml() returns a YAML string."""
        result = self.graph.schema_yaml("got")
        self.assertIsInstance(result, str)
        self.assertIn("got", result)

    def test_schema_yaml_contains_labels(self) -> None:
        """Schema YAML contains all node labels."""
        result = self.graph.schema_yaml("got")
        self.assertIn("Character", result)
        self.assertIn("Location", result)

    def test_schema_yaml_contains_relationships(self) -> None:
        """Schema YAML contains all relationship types."""
        result = self.graph.schema_yaml("got")
        self.assertIn("LIVES_IN", result)
        self.assertIn("KNOWS", result)
        self.assertIn("RULES", result)

    def test_schema_yaml_contains_properties(self) -> None:
        """Schema YAML contains node properties."""
        result = self.graph.schema_yaml("got")
        self.assertIn("name", result)
        self.assertIn("house", result)
        self.assertIn("age", result)

    def test_schema_yaml_contains_counts(self) -> None:
        """Schema YAML contains node/relationship counts."""
        result = self.graph.schema_yaml("got")
        self.assertIn("count: 4", result)  # 4 characters
        self.assertIn("count: 3", result)  # 3 locations
        self.assertIn("count: 2", result)  # 2 KNOWS
        self.assertIn("count: 3", result)  # 3 LIVES_IN
        self.assertIn("count: 1", result)  # 1 RULES

    def test_schema_yaml_valid_yaml(self) -> None:
        """Generated YAML is valid and parseable."""
        if yaml is None:
            self.skipTest("PyYAML not installed")
        result = self.graph.schema_yaml("got")
        data = yaml.safe_load(result)
        self.assertIsInstance(data, dict)
        self.assertIn("labels", data)
        self.assertIn("relationships", data)

    def test_schema_yaml_class_names(self) -> None:
        """Schema YAML contains Python class names."""
        result = self.graph.schema_yaml("got")
        self.assertIn("Character", result)
        self.assertIn("Location", result)
        self.assertIn("Knows", result)
        self.assertIn("LivesIn", result)
        self.assertIn("Rules", result)

    def test_schema_yaml_contains_relationship_properties(self) -> None:
        """Schema YAML contains properties stored on edges, not just nodes.

        KNOWS carries `strength`, RULES carries `since` — these live only
        in self._edge_attrs, never mirrored onto the raw networkx graph
        edge, so a naive read of the graph's own edge data would miss
        them entirely.
        """
        result = self.graph.schema_yaml("got")
        self.assertIn("strength", result)
        self.assertIn("since", result)

    def test_schema_yaml_empty_graph(self) -> None:
        """Schema YAML for an empty graph has empty labels/relationships."""
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "empty.pkl")
        g = NetworkXGraph(persistence_path=path)
        g._temp_path = tmp
        try:
            result = g.schema_yaml("empty")
            data = yaml.safe_load(result)
            self.assertEqual(data["labels"], {})
            self.assertEqual(data["relationships"], {})
        finally:
            g.close()
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Neo4jGraph — schema introspection (requires credentials)
# ---------------------------------------------------------------------------


class Neo4jSchemaTest(unittest.TestCase):
    """Tests for Neo4jGraph.schema_yaml()."""

    @classmethod
    def setUpClass(cls) -> None:
        password = os.environ.get("NEO4J_DEV_PASSWORD", "")
        if not password:
            cls.skip_reason = "NEO4J_DEV_PASSWORD not set"
            cls.graph = None
            return
        from cvcdocdb.neo4j_graph import Neo4jGraph
        cls.graph = None
        try:
            cls.graph = Neo4jGraph(
                url=os.environ.get("NEO4J_DEV_URL", "bolt://localhost:7687"),
                user=os.environ.get("NEO4J_DEV_USER", "neo4j"),
                password=password,
                database=os.environ.get("NEO4J_DEV_DATABASE", "neo4j"),
            )
        except Exception as e:
            cls.skip_reason = f"Cannot connect to Neo4j: {e}"
            cls.graph = None

    def setUp(self) -> None:
        if self.graph is None:
            self.skipTest("Neo4j not available")
        self.graph._tx = self.graph._session.begin_transaction()
        self.graph._tx.run("MATCH (n) DETACH DELETE n")
        self.graph._tx.commit()
        self.graph._tx = None

    def test_schema_yaml_returns_string(self) -> None:
        result = self.graph.schema_yaml("got")
        self.assertIsInstance(result, str)

    def test_schema_yaml_valid_yaml(self) -> None:
        if yaml is None:
            self.skipTest("PyYAML not installed")
        result = self.graph.schema_yaml("got")
        data = yaml.safe_load(result)
        self.assertIsInstance(data, dict)
        self.assertIn("labels", data)
        self.assertIn("relationships", data)

    def test_schema_yaml_relationship_src_dst_are_real_labels(self) -> None:
        """src/dst must be the endpoints' real main_label, not always 'Node'.

        The old code read sample["a"].get("labels", ("Node",))[0], which
        looks up a *property* literally named "labels" on the raw driver
        Node (never present on real data) instead of its real .labels
        attribute — so this always silently fell back to "Node".
        """
        if yaml is None:
            self.skipTest("PyYAML not installed")
        self.graph.insertNode(Node(pk={"name": "Jon Snow"}, main_label="Character"), replace=True)
        self.graph.insertNode(Node(pk={"name": "Winterfell"}, main_label="Location"), replace=True)
        self.graph.insertRelation(Relation(
            Node(pk={"name": "Jon Snow"}, main_label="Character"),
            Node(pk={"name": "Winterfell"}, main_label="Location"),
            "LIVES_IN",
        ), update=True)

        result = self.graph.schema_yaml("got")
        data = yaml.safe_load(result)
        rel = data["relationships"]["LIVES_IN"]
        self.assertEqual(rel["src"], "Character")
        self.assertEqual(rel["dst"], "Location")

    def test_schema_yaml_primary_key_is_not_an_arbitrary_guess(self) -> None:
        """Neo4j never persists which properties are the real pk (see
        migrate()'s docstring) — the old code guessed the first two
        property names in iteration order (pk_fields[:2]), which is
        arbitrary and wrong. Using every property is at least honest and
        consistent with migrate()'s documented fallback.
        """
        if yaml is None:
            self.skipTest("PyYAML not installed")
        self.graph.insertNode(
            Node(pk={"member_id": 1}, main_label="KarateMember", club="Mr. Hi", extra="z"),
            replace=True,
        )

        result = self.graph.schema_yaml("got")
        data = yaml.safe_load(result)
        pk_fields = set(data["labels"]["KarateMember"]["primary_key"])
        self.assertEqual(pk_fields, {"member_id", "club", "extra"})


if __name__ == "__main__":
    unittest.main()
