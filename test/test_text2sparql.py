"""Text2SPARQL (preguntes en llenguatge natural a SPARQL, per a JenaGraph) i
Text2Query (tria Text2SPARQL o Text2Cypher segons el backend).

Text2SPARQL té la mateixa API que Text2Cypher: el LLM rep l'esquema del
graf (expressat amb el vocabulari RDF de JenaGraph, amb els PREFIX del seu
espai de noms) i la pregunta, i la consulta que genera només s'executa si és
de lectura (SELECT/ASK/CONSTRUCT/DESCRIBE, sense SERVICE).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any, List

import pytest

from cvcdocdb.base import Node, Relation
from cvcdocdb.text2cypher import Text2CypherError
from cvcdocdb.text2query import Text2Query, Text2QueryError, Text2QueryResult
from cvcdocdb.text2sparql import (
    Text2SPARQL,
    Text2SPARQLError,
    Text2SPARQLResult,
    extract_sparql,
    format_sparql_schema,
    is_read_only_sparql,
)


class _RecordingLLM:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.prompts: List[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answer


class HelpersTest(unittest.TestCase):
    def test_read_only_queries(self) -> None:
        for query in (
            "SELECT ?s WHERE { ?s ?p ?o }",
            "PREFIX ex: <urn:x> ASK { ?s a ex:T }",
            "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }",
            "DESCRIBE <urn:x>",
            'SELECT ?s WHERE { ?s ?p "INSERT DATA { } and SERVICE" }',
        ):
            with self.subTest(query=query):
                self.assertTrue(is_read_only_sparql(query))

    def test_writes_and_federation_are_not_read_only(self) -> None:
        for query in (
            "INSERT DATA { <urn:a> <urn:b> <urn:c> }",
            "DELETE WHERE { ?s ?p ?o }",
            "PREFIX ex: <urn:x> DELETE { ?s ?p ?o } WHERE { ?s ?p ?o }",
            "CLEAR ALL",
            "DROP GRAPH <urn:g>",
            "LOAD <http://example.org/data.ttl>",
            "SELECT * WHERE { SERVICE <http://evil.example/sparql> { ?s ?p ?o } }",
            "MATCH (n) RETURN n",
            "SELECT ?s WHERE { ?s ?p ?o } ; INSERT DATA { <urn:a> <urn:b> <urn:c> }",
        ):
            with self.subTest(query=query):
                self.assertFalse(is_read_only_sparql(query))

    def test_extract_from_code_fence(self) -> None:
        self.assertEqual(extract_sparql("```sparql\nSELECT * WHERE { ?s ?p ?o }\n```"), "SELECT * WHERE { ?s ?p ?o }")
        self.assertEqual(extract_sparql("  ASK { ?s ?p ?o } "), "ASK { ?s ?p ?o }")

    def test_schema_uses_the_graph_vocabulary(self) -> None:
        schema = format_sparql_schema(
            "urn:cvcdocdb:",
            {"Document": [("title", "STRING"), ("year", "INTEGER")], "Page": [("num", "INTEGER")]},
            {"HAS_PAGE": [("order", "INTEGER")]},
            [("Document", "HAS_PAGE", "Page")],
        )
        self.assertIn("PREFIX label: <urn:cvcdocdb:label/>", schema)
        self.assertIn("PREFIX prop: <urn:cvcdocdb:prop/>", schema)
        self.assertIn("PREFIX rel: <urn:cvcdocdb:rel/>", schema)
        self.assertIn("label:Document {prop:title: STRING, prop:year: INTEGER}", schema)
        self.assertIn("rel:HAS_PAGE {prop:order: INTEGER}", schema)
        self.assertIn("?a a label:Document . ?a rel:HAS_PAGE ?b . ?b a label:Page", schema)

    def test_names_that_are_not_valid_prefixed_names_use_full_iris(self) -> None:
        schema = format_sparql_schema("urn:x:", {"Doc": [("a b", "STRING")]}, {}, [])
        self.assertIn("<urn:x:prop/a%20b>: STRING", schema)

    def test_error_hierarchy(self) -> None:
        self.assertTrue(issubclass(Text2SPARQLError, Text2QueryError))
        self.assertTrue(issubclass(Text2CypherError, Text2QueryError))

    def test_text2sparql_needs_a_sparql_backend(self) -> None:
        from cvcdocdb.networkx_graph import NetworkXGraph

        graph = NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "g.pkl"))
        with self.assertRaises(TypeError):
            Text2SPARQL(graph, llm=lambda p: "ASK { ?s ?p ?o }")

    def test_lazy_exports(self) -> None:
        import cvcdocdb

        self.assertIs(cvcdocdb.Text2SPARQL, Text2SPARQL)
        self.assertIs(cvcdocdb.Text2Query, Text2Query)
        self.assertIs(cvcdocdb.Text2QueryError, Text2QueryError)


class Text2QueryOnNetworkXTest(unittest.TestCase):
    def test_uses_text2cypher(self) -> None:
        from cvcdocdb.networkx_graph import NetworkXGraph
        from cvcdocdb.text2cypher import Text2Cypher

        graph = NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "g.pkl"))
        graph.insertNode(Node(pk={"id": 1}, main_label="T"))
        t2q = Text2Query(graph, llm=lambda p: "MATCH (n:T) RETURN count(n) AS c")
        self.assertEqual(t2q.language, "cypher")
        self.assertIsInstance(t2q.translator, Text2Cypher)
        result = t2q.query("how many?")
        self.assertIsInstance(result, Text2QueryResult)
        self.assertEqual((result.language, result.query, result.records), ("cypher", "MATCH (n:T) RETURN count(n) AS c", [{"c": 1}]))

    def test_errors_are_text2query_errors(self) -> None:
        from cvcdocdb.networkx_graph import NetworkXGraph

        graph = NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "g.pkl"))
        with self.assertRaises(Text2QueryError):
            Text2Query(graph, llm=lambda p: "MATCH (n) DETACH DELETE n").query("delete")


# ---------------------------------------------------------------------------
# Amb Fuseki
# ---------------------------------------------------------------------------


@pytest.mark.slow
class Text2SPARQLOnJenaTest(unittest.TestCase):
    def setUp(self) -> None:
        from test.test_jena_graph import _fresh_jena

        self.graph = _fresh_jena()
        self.ns = self.graph._mapping.namespace
        doc = Node(pk={"doc": "D1"}, main_label="Document", title="Padró 1905")
        self.graph.insertNode(doc)
        for i in (1, 2):
            page = Node(pk={"page": f"P{i}"}, main_label="Page", num=i)
            self.graph.insertNode(page)
            self.graph.insertRelation(Relation(src=doc, dst=page, rel_type="HAS_PAGE", order=i))

    def tearDown(self) -> None:
        self.graph.close()

    def test_query_runs_the_llm_sparql_and_returns_rows(self) -> None:
        sparql = "PREFIX label: <%slabel/> SELECT (COUNT(?p) AS ?c) WHERE { ?p a label:Page }" % self.ns
        llm = _RecordingLLM(f"```sparql\n{sparql}\n```")
        result = Text2SPARQL(self.graph, llm=llm).query("How many pages are there?")
        self.assertIsInstance(result, Text2SPARQLResult)
        self.assertEqual(result.sparql, sparql)
        self.assertEqual(result.records, [{"c": 2}])

    def test_prompt_contains_question_and_schema(self) -> None:
        llm = _RecordingLLM("ASK { ?s ?p ?o }")
        t2s = Text2SPARQL(self.graph, llm=llm)
        t2s.query("Which pages does the document have?")
        prompt = llm.prompts[0]
        self.assertIn("Which pages does the document have?", prompt)
        self.assertIn(f"PREFIX label: <{self.ns}label/>", prompt)
        self.assertIn("label:Document {prop:doc: STRING, prop:title: STRING}", prompt)
        self.assertIn("rel:HAS_PAGE {prop:order: INTEGER}", prompt)
        self.assertIn(t2s.schema, prompt)
        self.assertIn("SPARQL", prompt)

    def test_write_queries_are_refused_and_nothing_changes(self) -> None:
        t2s = Text2SPARQL(self.graph, llm=lambda p: "DELETE WHERE { ?s ?p ?o }")
        with self.assertRaises(Text2SPARQLError):
            t2s.query("delete everything")
        self.assertEqual(len(self.graph.get_node_ids()), 3)

    def test_federated_queries_are_refused(self) -> None:
        t2s = Text2SPARQL(self.graph, llm=lambda p: "SELECT * WHERE { SERVICE <http://evil.example/sparql> { ?s ?p ?o } }")
        with self.assertRaises(Text2SPARQLError):
            t2s.query("?")

    def test_invalid_sparql_raises_text2sparql_error(self) -> None:
        t2s = Text2SPARQL(self.graph, llm=lambda p: "SELECT ?s WHERE { ?s ?p ")
        with self.assertRaises(Text2SPARQLError):
            t2s.query("?")

    def test_explicit_schema_examples_and_custom_prompt(self) -> None:
        llm = _RecordingLLM("ASK { ?s ?p ?o }")
        t2s = Text2SPARQL(self.graph, llm=llm, schema="MY SCHEMA", examples=["Q: n? A: ASK {}"],
                          custom_prompt="S={schema}\nE={examples}\nQ={query_text}\nSPARQL:")
        t2s.query("q1")
        self.assertEqual(llm.prompts[0], "S=MY SCHEMA\nE=Q: n? A: ASK {}\nQ=q1\nSPARQL:")

    def test_text2query_picks_text2sparql(self) -> None:
        sparql = "PREFIX label: <%slabel/> SELECT (COUNT(?p) AS ?c) WHERE { ?p a label:Page }" % self.ns
        t2q = Text2Query(self.graph, llm=lambda p: sparql)
        self.assertEqual(t2q.language, "sparql")
        self.assertIsInstance(t2q.translator, Text2SPARQL)
        result = t2q.query("pages?")
        self.assertEqual((result.language, result.query, result.records), ("sparql", sparql, [{"c": 2}]))
        with self.assertRaises(Text2QueryError):
            Text2Query(self.graph, llm=lambda p: "CLEAR ALL").query("x")
