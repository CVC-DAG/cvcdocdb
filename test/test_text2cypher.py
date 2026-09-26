"""Tests for cvcdocdb.text2cypher — natural-language → Cypher over any backend.

The LLM is always a fake (a plain Python callable returning a fixed Cypher
string), so these tests never contact a real LLM provider.

- ``unit`` tests need nothing but cvcdocdb.
- ``TestText2CypherPortable`` runs the *same* client code against every
  available backend: ``NetworkXGraph`` always, ``Neo4jGraph`` when a real
  Neo4j and the optional ``neo4j-graphrag`` package are available.
"""

import asyncio
import importlib.util
import os
import socket
import sys
import uuid
from urllib.parse import urlparse

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cvcdocdb.base import Node, Relation  # noqa: E402
from cvcdocdb.text2cypher import (  # noqa: E402
    CallableLLM,
    LLMResponse,
    Text2Cypher,
    Text2CypherError,
    Text2CypherResult,
    extract_cypher,
    format_schema,
    is_read_only_cypher,
)

HAS_GRAPHRAG = importlib.util.find_spec("neo4j_graphrag") is not None


class _RecordingLLM:
    """Callable fake LLM: records every prompt, always answers *answer*."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.prompts = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answer


# ----------------------------------------------------------------------
# unit
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestCallableLLM:
    def test_str_input_returns_response_with_content(self):
        resp = CallableLLM(lambda prompt: f"echo: {prompt}").invoke("hola")
        assert isinstance(resp, LLMResponse)
        assert resp.content == "echo: hola"

    def test_message_list_input_is_flattened_to_a_prompt(self):
        seen = []
        llm = CallableLLM(lambda prompt: seen.append(prompt) or "ok")
        msgs = [
            {"role": "system", "content": "You write Cypher."},
            {"role": "user", "content": "Count documents"},
        ]
        assert llm.invoke(msgs).content == "ok"
        assert "You write Cypher." in seen[0]
        assert "Count documents" in seen[0]

    def test_system_instruction_is_prepended(self):
        seen = []
        llm = CallableLLM(lambda prompt: seen.append(prompt) or "ok")
        llm.invoke("question", system_instruction="Be terse.")
        assert seen[0].index("Be terse.") < seen[0].index("question")

    def test_ainvoke(self):
        llm = CallableLLM(lambda prompt: prompt.upper())
        assert asyncio.run(llm.ainvoke("abc")).content == "ABC"

    def test_non_str_answer_is_rejected(self):
        with pytest.raises(TypeError):
            CallableLLM(lambda prompt: 42).invoke("x")

    def test_non_callable_is_rejected(self):
        with pytest.raises(TypeError):
            CallableLLM("not a function")


@pytest.mark.unit
class TestHelpers:
    def test_format_schema(self):
        text = format_schema(
            node_props={"Document": [("doc", "STRING"), ("pages", "INTEGER")]},
            rel_props={"HAS_PAGE": [("order", "INTEGER")]},
            relationships=[("Document", "HAS_PAGE", "Page")],
        )
        assert "Node properties:" in text
        assert "Document {doc: STRING, pages: INTEGER}" in text
        assert "HAS_PAGE {order: INTEGER}" in text
        assert "(:Document)-[:HAS_PAGE]->(:Page)" in text

    def test_extract_cypher_from_code_fence(self):
        assert extract_cypher("Sure:\n```cypher\nMATCH (n) RETURN n\n```") == "MATCH (n) RETURN n"
        assert extract_cypher("  MATCH (n) RETURN n  ") == "MATCH (n) RETURN n"

    @pytest.mark.parametrize(
        "cypher",
        [
            "MATCH (n) RETURN n",
            "MATCH (n:Doc {title: 'CREATE a SET'}) RETURN count(n) AS c",
            "MATCH (n) WHERE n.status = \"DELETE\" RETURN n.offset AS o",
        ],
    )
    def test_read_only(self, cypher):
        assert is_read_only_cypher(cypher)

    @pytest.mark.parametrize(
        "cypher",
        [
            "CREATE (n:Doc)",
            "MATCH (n) DETACH DELETE n",
            "MATCH (n) SET n.x = 1",
            "MERGE (n:Doc {id: 1})",
            "MATCH (n) REMOVE n.x",
            "CALL db.clearQueryCaches()",
            "LOAD CSV FROM 'file:///x' AS row RETURN row",
            "MATCH (n) FOREACH (x IN [1] | SET n.y = x)",
            "DROP INDEX foo",
        ],
    )
    def test_not_read_only(self, cypher):
        assert not is_read_only_cypher(cypher)


@pytest.mark.unit
class TestText2CypherValidation:
    def test_rejects_graph_without_query(self):
        with pytest.raises(TypeError, match="query"):
            Text2Cypher(object(), llm=lambda p: "RETURN 1")

    def test_rejects_llm_without_invoke_and_not_callable(self, tmp_path):
        from cvcdocdb.networkx_graph import NetworkXGraph

        graph = NetworkXGraph(persistence_path=str(tmp_path / "g.pkl"))
        with pytest.raises(TypeError, match="llm"):
            Text2Cypher(graph, llm=object())


def test_lazy_export_from_package():
    import cvcdocdb

    assert cvcdocdb.Text2Cypher is Text2Cypher
    assert cvcdocdb.Text2CypherError is Text2CypherError
    assert cvcdocdb.CallableLLM is CallableLLM


# ----------------------------------------------------------------------
# Portable: the same client code against every backend
# ----------------------------------------------------------------------


def _neo4j_config():
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
    )
    if os.path.exists(env_path):
        from dotenv import load_dotenv

        load_dotenv(env_path)
    target = os.environ.get("NEO4J_TARGET", "DEV").upper()

    def _env(key):
        return os.environ.get(f"NEO4J_{target}_{key}") or os.environ.get(f"NEO4J_{key}")

    if not (_env("URL") and _env("USER") and _env("PASSWORD")):
        return None
    parsed = urlparse(_env("URL"))
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 7687), 0.5):
            pass
    except OSError:
        return None
    return dict(
        url=_env("URL"),
        user=_env("USER"),
        password=_env("PASSWORD"),
        database=_env("DATABASE"),
    )


@pytest.fixture(
    params=[
        "networkx",
        pytest.param("neo4j", marks=pytest.mark.slow),
    ]
)
def populated_graph(request, tmp_path):
    """A small Document -HAS_PAGE-> Page graph on the requested backend."""
    suffix = uuid.uuid4().hex[:8]
    doc_label, page_label = f"T2CDoc{suffix}", f"T2CPage{suffix}"

    if request.param == "networkx":
        from cvcdocdb.networkx_graph import NetworkXGraph

        graph = NetworkXGraph(persistence_path=str(tmp_path / "g.pkl"))
        cleanup = None
    else:
        if not HAS_GRAPHRAG:
            pytest.skip("neo4j-graphrag not installed")
        config = _neo4j_config()
        if config is None:
            pytest.skip("Neo4j not reachable")
        from cvcdocdb.neo4j_graph import Neo4jGraph

        graph = Neo4jGraph(**config)

        def cleanup():
            graph.query(
                f"MATCH (n) WHERE n:{doc_label} OR n:{page_label} DETACH DELETE n"
            )

    doc = Node(pk={"doc": "D1"}, main_label=doc_label, title="Padró 1905")
    graph.insertNode(doc, replace=True)
    for i in (1, 2):
        page = Node(pk={"page": f"P{i}"}, main_label=page_label, num=i)
        graph.insertNode(page, replace=True)
        graph.insertRelation(Relation(src=doc, dst=page, rel_type="HAS_PAGE", order=i))
    try:
        yield graph, doc_label, page_label
    finally:
        if cleanup is not None:
            cleanup()
        graph.close()


class TestText2CypherPortable:
    """Only uses the backend-independent API: no backend-specific code."""

    def test_query_runs_llm_cypher_and_returns_rows(self, populated_graph):
        graph, doc_label, page_label = populated_graph
        cypher = f"MATCH (p:{page_label}) RETURN count(p) AS c"
        llm = _RecordingLLM(f"```cypher\n{cypher}\n```")

        result = Text2Cypher(graph, llm=llm).query("How many pages are there?")

        assert isinstance(result, Text2CypherResult)
        assert result.cypher == cypher
        assert result.records == [{"c": 2}]

    def test_prompt_contains_question_and_schema(self, populated_graph):
        graph, doc_label, page_label = populated_graph
        llm = _RecordingLLM("RETURN 1 AS x")
        t2c = Text2Cypher(graph, llm=llm)
        t2c.query("Which pages does the document have?")

        prompt = llm.prompts[0]
        assert "Which pages does the document have?" in prompt
        assert f"{doc_label} {{" in prompt and "title: STRING" in prompt
        assert f"{page_label} {{" in prompt and "num: INTEGER" in prompt
        assert "HAS_PAGE {order: INTEGER}" in prompt
        assert f"(:{doc_label})-[:HAS_PAGE]->(:{page_label})" in prompt
        assert t2c.schema in prompt

    def test_property_filter(self, populated_graph):
        graph, doc_label, _ = populated_graph
        llm = _RecordingLLM(f"MATCH (d:{doc_label} {{doc: 'D1'}}) RETURN d.title AS t")
        assert Text2Cypher(graph, llm=llm).query("title?").records == [{"t": "Padró 1905"}]

    def test_explicit_schema_and_examples_reach_the_prompt(self, populated_graph):
        graph, _, _ = populated_graph
        llm = _RecordingLLM("RETURN 1 AS x")
        t2c = Text2Cypher(
            graph,
            llm=llm,
            schema="MY CUSTOM SCHEMA",
            examples=["Q: how many docs? A: MATCH (d:Doc) RETURN count(d)"],
        )
        t2c.query("anything")
        assert t2c.schema == "MY CUSTOM SCHEMA"
        assert "MY CUSTOM SCHEMA" in llm.prompts[0]
        assert "how many docs?" in llm.prompts[0]

    def test_custom_prompt(self, populated_graph):
        graph, _, _ = populated_graph
        llm = _RecordingLLM("RETURN 1 AS x")
        Text2Cypher(
            graph, llm=llm, custom_prompt="S={schema}\nQ={query_text}\nCYPHER:"
        ).query("q1")
        assert llm.prompts[0].startswith("S=")
        assert "Q=q1\nCYPHER:" in llm.prompts[0]

    def test_llm_object_with_invoke_is_accepted(self, populated_graph):
        graph, _, page_label = populated_graph

        class _MyLLM:
            def invoke(self, input, *args, **kwargs):
                return LLMResponse(content=f"MATCH (p:{page_label}) RETURN count(p) AS n")

            async def ainvoke(self, input, *args, **kwargs):
                return self.invoke(input)

        assert Text2Cypher(graph, llm=_MyLLM()).query("?").records == [{"n": 2}]

    def test_write_queries_are_refused(self, populated_graph):
        graph, _, page_label = populated_graph
        t2c = Text2Cypher(graph, llm=lambda p: f"MATCH (n:{page_label}) DETACH DELETE n")
        with pytest.raises(Text2CypherError):
            t2c.query("delete everything")
        assert graph.query(f"MATCH (p:{page_label}) RETURN count(p) AS c") == [{"c": 2}]


# ----------------------------------------------------------------------
# Backend-specific details
# ----------------------------------------------------------------------


@pytest.mark.slow
def test_neo4j_uses_text2cypher_retriever_on_graph_driver(populated_graph_neo4j):
    graph = populated_graph_neo4j
    from neo4j_graphrag.retrievers import Text2CypherRetriever

    t2c = Text2Cypher(graph, llm=lambda p: "RETURN 1")
    assert isinstance(t2c.retriever, Text2CypherRetriever)
    assert t2c.retriever.driver is graph._driver


@pytest.fixture
def populated_graph_neo4j():
    if not HAS_GRAPHRAG:
        pytest.skip("neo4j-graphrag not installed")
    config = _neo4j_config()
    if config is None:
        pytest.skip("Neo4j not reachable")
    from cvcdocdb.neo4j_graph import Neo4jGraph

    graph = Neo4jGraph(**config)
    yield graph
    graph.close()


@pytest.mark.integration
def test_networkx_unsupported_cypher_raises_text2cypher_error(tmp_path):
    from cvcdocdb.networkx_graph import NetworkXGraph

    graph = NetworkXGraph(persistence_path=str(tmp_path / "g.pkl"))
    t2c = Text2Cypher(graph, llm=lambda p: "MATCH (a)-[*1..2]->(b) RETURN b")
    with pytest.raises(Text2CypherError, match="not supported"):
        t2c.query("reachable nodes?")


@pytest.mark.integration
def test_networkx_needs_no_retriever(tmp_path):
    from cvcdocdb.networkx_graph import NetworkXGraph

    graph = NetworkXGraph(persistence_path=str(tmp_path / "g.pkl"))
    assert Text2Cypher(graph, llm=lambda p: "RETURN 1").retriever is None
