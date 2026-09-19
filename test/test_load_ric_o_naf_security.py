"""Security tests for the RiC-O/NAF example loader's Cypher generation.

_import_nodes_via_cypher / _import_rels_via_cypher build Cypher queries
from data extracted out of RDF/XML files downloaded live from GitHub.
node ids, property names, and property values are attacker-influenceable
if that upstream content is ever malicious — they must never be spliced
as raw string literals into the query text.
"""

from __future__ import annotations

import unittest

from cvcdocdb.exemples.load_ric_o_naf import (
    _import_nodes_via_cypher,
    _import_rels_via_cypher,
)


class _FakeGraph:
    """Records every query/params pair passed to .query() without executing anything."""

    def __init__(self) -> None:
        self.calls = []

    def query(self, query, params=None):
        self.calls.append((query, params or {}))
        return []


class ImportNodesSecurityTest(unittest.TestCase):
    def test_malicious_node_id_is_not_spliced_into_query_text(self) -> None:
        graph = _FakeGraph()
        nodes = [
            {
                "id": 'x"}) DETACH DELETE n //',
                "labels": ["Agent"],
                "properties": {},
            }
        ]
        _import_nodes_via_cypher(graph, nodes)

        self.assertEqual(len(graph.calls), 1)
        query, params = graph.calls[0]
        self.assertNotIn('DETACH DELETE', query)
        self.assertIn('x"}) DETACH DELETE n //', str(params))

    def test_malicious_property_value_is_not_spliced_into_query_text(self) -> None:
        graph = _FakeGraph()
        nodes = [
            {
                "id": "agent1",
                "labels": ["Agent"],
                "properties": {"name": 'x" SET n.pwned = true //'},
            }
        ]
        _import_nodes_via_cypher(graph, nodes)

        query, params = graph.calls[0]
        self.assertNotIn("pwned", query)


class ImportRelsSecurityTest(unittest.TestCase):
    def test_malicious_src_dst_id_is_not_spliced_into_query_text(self) -> None:
        graph = _FakeGraph()
        rels = [
            {
                "src_id": 'a"}) DETACH DELETE a //',
                "dst_id": "b",
                "type": "HAS_AGENT",
                "src_label": "Thing",
                "dst_label": "Agent",
            }
        ]
        _import_rels_via_cypher(graph, rels)

        self.assertEqual(len(graph.calls), 1)
        query, params = graph.calls[0]
        self.assertNotIn("DETACH DELETE", query)


if __name__ == "__main__":
    unittest.main()
