"""Tests for the GraphStore.query() hybrid method.

The query() method accepts either:
- A **dict** → MongoDB-style filter (NetworkXGraph only)
- A **str** → Cypher query (NetworkXGraph + Neo4jGraph)

With kwargs: projection, sort, limit_val (MongoDB-style) or params (Cypher).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List

from cvcdocdb.base import Node, Relation, WeakNode
from cvcdocdb.graph_store import GraphStore
from cvcdocdb.networkx_graph import NetworkXGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_graph() -> NetworkXGraph:
    """Return a fresh NetworkXGraph with sample data.

    Uses a temporary persistence directory so each test run starts clean.
    """
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "test_graph.pkl")
    g = NetworkXGraph(persistence_path=path)
    g._temp_path = tmp  # track for cleanup

    # Insert sample nodes
    g.insertNode(
        Node(pk={"id": 1}, main_label="Person", name="Alice", age=30),
        replace=True,
    )
    g.insertNode(
        Node(pk={"id": 2}, main_label="Person", name="Bob", age=25),
        replace=True,
    )
    g.insertNode(
        Node(pk={"id": 3}, main_label="Company", name="Acme", industry="Tech"),
        replace=True,
    )
    # Relations
    g.insertRelation(
        Relation(
            Node(pk={"id": 1}, main_label="Person"),
            Node(pk={"id": 3}, main_label="Company"),
            "WORKS_AT",
        ),
        update=True,
    )
    g.insertRelation(
        Relation(
            Node(pk={"id": 2}, main_label="Person"),
            Node(pk={"id": 3}, main_label="Company"),
            "WORKS_AT",
        ),
        update=True,
    )
    return g


# ---------------------------------------------------------------------------
# NetworkXGraph — MongoDB-style dict queries
# ---------------------------------------------------------------------------


class MongoDBQueryTest(unittest.TestCase):
    """Tests for NetworkXGraph.query() with dict filters."""

    def setUp(self) -> None:
        self.graph = _make_graph()

    def tearDown(self) -> None:
        path = getattr(self.graph, "_temp_path", None)
        try:
            self.graph.close()
        except Exception:
            pass
        if path and os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)

    def test_query_no_filter_returns_all(self) -> None:
        result = self.graph.query()
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 3)

    def test_query_dict_filter(self) -> None:
        result = self.graph.query({"main_label": "Person"})
        self.assertEqual(len(result), 2)

    def test_query_dict_filter_with_properties(self) -> None:
        result = self.graph.query({"age": 30})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["properties"]["name"], "Alice")

    def test_query_dict_with_operator(self) -> None:
        result = self.graph.query({"age": {"$gt": 26}})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["properties"]["name"], "Alice")

    def test_query_dict_with_projection(self) -> None:
        result = self.graph.query(
            {"main_label": "Person"}, projection={"name": 1}
        )
        self.assertEqual(len(result), 2)
        for r in result:
            self.assertIn("name", r["properties"])
            self.assertNotIn("age", r["properties"])

    def test_query_dict_with_sort(self) -> None:
        result = self.graph.query(
            {"main_label": "Person"}, sort=("properties.name", 1)
        )
        self.assertEqual(result[0]["properties"]["name"], "Alice")
        self.assertEqual(result[1]["properties"]["name"], "Bob")

    def test_query_dict_with_limit(self) -> None:
        result = self.graph.query({"main_label": "Person"}, limit_val=1)
        self.assertEqual(len(result), 1)

    def test_query_dict_with_in_operator(self) -> None:
        result = self.graph.query(
            {"name": {"$in": ["Alice", "Bob"]}}
        )
        self.assertEqual(len(result), 2)

    def test_query_dict_with_ne_operator(self) -> None:
        result = self.graph.query({"main_label": "Person", "name": {"$ne": "Alice"}})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["properties"]["name"], "Bob")

    def test_query_dict_with_regex(self) -> None:
        result = self.graph.query(
            {"main_label": "Person", "name": {"$regex": "^A"}}
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["properties"]["name"], "Alice")

    def test_query_dict_with_exists_true(self) -> None:
        result = self.graph.query(
            {"age": {"$exists": True}}
        )
        self.assertGreaterEqual(len(result), 2)

    def test_query_dict_with_exists_false(self) -> None:
        result = self.graph.query(
            {"properties.nonexistent": {"$exists": False}}
        )
        self.assertGreaterEqual(len(result), 2)


# ---------------------------------------------------------------------------
# NetworkXGraph — Cypher-style queries
# ---------------------------------------------------------------------------


class CypherQueryTest(unittest.TestCase):
    """Tests for NetworkXGraph.query() with Cypher strings."""

    def setUp(self) -> None:
        self.graph = _make_graph()

    def tearDown(self) -> None:
        path = getattr(self.graph, "_temp_path", None)
        try:
            self.graph.close()
        except Exception:
            pass
        if path and os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)

    def test_query_match_all(self) -> None:
        result = self.graph.query("MATCH (n) RETURN n")
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 3)

    def test_query_match_with_label(self) -> None:
        result = self.graph.query("MATCH (n:Person) RETURN n")
        self.assertEqual(len(result), 2)

    def test_query_count(self) -> None:
        result = self.graph.query("MATCH (n) RETURN count(n) AS total")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["total"], 3)

    def test_query_match_with_params(self) -> None:
        result = self.graph.query(
            "MATCH (n:Person {name: $name}) RETURN n",
            params={"name": "Alice"},
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["n"]["properties"]["name"], "Alice")

    def test_query_match_edges(self) -> None:
        result = self.graph.query(
            "MATCH (a)-[r]->(b) RETURN a, type(r) AS rel_type, b"
        )
        # Should find the WORKS_AT relations we created
        works_at = [r for r in result if r.get("rel_type") == "WORKS_AT"]
        self.assertGreaterEqual(len(works_at), 2)

    def test_query_complex_expression(self) -> None:
        result = self.graph.query(
            "MATCH (n:Person) RETURN n.name AS person_name, n.age AS person_age"
        )
        self.assertEqual(len(result), 2)
        names = {r["person_name"] for r in result}
        self.assertEqual(names, {"Alice", "Bob"})

    def test_query_create_node(self) -> None:
        self.graph.query("CREATE (n:TestNode {id: 999, label: 'test'})")
        result = self.graph.query("MATCH (n:TestNode) RETURN n")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["n"]["properties"]["label"], "test")

    def test_query_set_node(self) -> None:
        self.graph.query("CREATE (n:TestNode {id: 777, age: 10})")
        self.graph.query("MATCH (n:TestNode {id: 777}) SET n.age = 20")
        result = self.graph.query("MATCH (n:TestNode {id: 777}) RETURN n")
        self.assertEqual(result[0]["n"]["properties"]["age"], 20)

    def test_query_delete_node(self) -> None:
        self.graph.query("CREATE (n:TestNode {id: 999, label: 'test'})")
        self.graph.query("MATCH (n:TestNode {label: 'test'}) DETACH DELETE n")
        result = self.graph.query("MATCH (n:TestNode {label: 'test'}) RETURN n")
        self.assertEqual(result, [])

    def test_query_with_no_results(self) -> None:
        result = self.graph.query(
            "MATCH (n:Company {industry: 'Finance'}) RETURN n"
        )
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# Neo4jGraph — requires Neo4j connection
# ---------------------------------------------------------------------------


class Neo4jQueryTest(unittest.TestCase):
    """Tests for Neo4jGraph.query() (requires Neo4j)."""

    @classmethod
    def setUpClass(cls) -> None:
        from cvcdocdb.neo4j_graph import Neo4jGraph

        cls.password = os.environ.get("NEO4J_DEV_PASSWORD", "")
        if not cls.password:
            cls.skip_reason = "NEO4J_DEV_PASSWORD not set"
            cls.graph = None
            return

        cls.graph = None
        try:
            cls.graph = Neo4jGraph(
                url=os.environ.get("NEO4J_DEV_URL", "bolt://localhost:7687"),
                user=os.environ.get("NEO4J_DEV_USER", "neo4j"),
                password=cls.password,
                database=os.environ.get("NEO4J_DEV_DATABASE", "neo4j"),
            )
        except Exception as e:
            cls.skip_reason = f"Cannot connect to Neo4j: {e}"
            cls.graph = None

    def setUp(self) -> None:
        if self.graph is None:
            self.skipTest("Neo4j not available")

    def test_query_returns_list(self) -> None:
        result = self.graph.query("MATCH (n) RETURN n LIMIT 1")
        self.assertIsInstance(result, list)

    def test_query_count_nodes(self) -> None:
        result = self.graph.query("MATCH (n) RETURN count(n) AS total")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0]["total"], int)

    def test_query_with_params(self) -> None:
        result = self.graph.query(
            "MATCH (n:Person {name: $name}) RETURN n",
            params={"name": "Alice"},
        )
        self.assertIsInstance(result, list)

    def test_query_dict_raises_value_error(self) -> None:
        """Neo4jGraph.query() should reject dict filters."""
        with self.assertRaises(ValueError):
            self.graph.query({"name": "Alice"})

    def test_query_return_node_converts_to_dict_with_properties(self) -> None:
        """RETURN n must convert the real driver Node into the documented
        {"labels": [...], "properties": {...}} dict — not the raw
        neo4j.graph.Node object, which has no `.properties` attribute."""
        self.graph.query(
            "CREATE (n:QueryConversionTest {name: 'Alice', age: 30})"
        )
        try:
            result = self.graph.query(
                "MATCH (n:QueryConversionTest) RETURN n"
            )
            self.assertEqual(len(result), 1)
            node = result[0]["n"]
            self.assertIsInstance(node, dict)
            self.assertEqual(node["labels"], ["QueryConversionTest"])
            self.assertEqual(node["properties"]["name"], "Alice")
            self.assertEqual(node["properties"]["age"], 30)
        finally:
            self.graph.query(
                "MATCH (n:QueryConversionTest) DETACH DELETE n"
            )


if __name__ == "__main__":
    unittest.main()
