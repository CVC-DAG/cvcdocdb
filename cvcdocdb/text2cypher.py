"""Natural-language questions over any cvcdocdb graph backend.

:class:`Text2Cypher` gives the same API whatever the backend, so a script
doesn't change when the graph does::

    from cvcdocdb import Text2Cypher

    t2c = Text2Cypher(graph, llm=my_llm)     # graph: Neo4jGraph, NetworkXGraph...
    result = t2c.query("Quantes persones viuen a Caldes d'Estrac?")
    result.cypher    # "MATCH (i:IndividuPadro)-[:VIU_A]->(l:LlocPadro ...) ..."
    result.records   # [{'n': 42}] — same shape as graph.query(cypher)

An LLM translates the question to Cypher, which is then run on the graph.
Only read-only Cypher is ever executed; anything else raises
:class:`Text2CypherError`.

The LLM is a *handle* that already carries its own configuration (API key,
endpoint, model...), so nothing else has to be passed:

- a plain Python callable ``prompt: str -> str`` (wrapped in
  :class:`CallableLLM`), e.g. a wrapper around any in-house LLM client, or
- any object with an ``invoke(prompt)`` method returning something with a
  ``.content`` string — e.g. the ``neo4j_graphrag`` LLM classes.

Backends:

- ``Neo4jGraph`` — delegates to ``neo4j_graphrag``'s ``Text2CypherRetriever``
  on the graph's own driver and database (no Neo4j credentials passed
  again), which checks the query with ``EXPLAIN`` before running it.
  Requires the optional dependency: ``pip install cvcdocdb[graphrag]``.
- Any other backend with a Cypher-capable ``query()`` (e.g.
  ``NetworkXGraph``) — handled by cvcdocdb itself, no extra dependency; the
  query is checked for write clauses and run with ``graph.query()``. On
  ``NetworkXGraph`` that evaluates the common read-only Cypher (patterns,
  ``WHERE``, ``WITH``, aggregations, ``ORDER BY``...) with Neo4j semantics
  (see :mod:`cvcdocdb.nx_cypher`); a query using syntax it doesn't support
  (variable-length paths, ``UNWIND``, ``CASE``...) raises
  :class:`Text2CypherError` instead of returning wrong results.
"""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "CallableLLM",
    "LLMResponse",
    "Text2Cypher",
    "Text2CypherError",
    "Text2CypherResult",
    "extract_cypher",
    "format_schema",
    "is_read_only_cypher",
]

_INSTALL_HINT = (
    "Text2Cypher on a Neo4jGraph requires the optional 'neo4j-graphrag' "
    "package: pip install cvcdocdb[graphrag]"
)

# Same wording as neo4j_graphrag's default Text2Cypher prompt, so both
# backends ask the LLM the same thing.
DEFAULT_PROMPT = """Task: Generate a Cypher statement for querying a graph database from a user input.

Schema:
{schema}

Examples (optional):
{examples}

Input:
{query_text}

Do not use any properties or relationships not included in the schema.
Do not include triple backticks ``` or any additional text except the generated Cypher statement in your response.

Cypher query:
"""

# Node attributes cvcdocdb keeps for its own bookkeeping, not user data.
_INTERNAL_PROPERTIES = {"pk", "main_label", "labels"}

_WRITE_CLAUSE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|CALL|LOAD\s+CSV)\b",
    re.IGNORECASE,
)
_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`")

# db.schema.*TypeProperties() / Python type names -> schema type names
# (the ones neo4j_graphrag uses in its own schema text).
_TYPE_NAMES = {
    "String": "STRING",
    "Long": "INTEGER",
    "Integer": "INTEGER",
    "Double": "FLOAT",
    "Float": "FLOAT",
    "Boolean": "BOOLEAN",
    "Date": "DATE",
    "DateTime": "DATE_TIME",
    "LocalDateTime": "LOCAL_DATE_TIME",
    "Time": "TIME",
    "LocalTime": "LOCAL_TIME",
    "Duration": "DURATION",
    "Point": "POINT",
    "str": "STRING",
    "int": "INTEGER",
    "float": "FLOAT",
    "bool": "BOOLEAN",
    "list": "LIST",
    "tuple": "LIST",
    "dict": "MAP",
    "date": "DATE",
    "datetime": "DATE_TIME",
}


class Text2CypherError(Exception):
    """The LLM's answer could not be run: invalid or non-read-only Cypher."""


@dataclass
class LLMResponse:
    """Minimal LLM answer: just its text ``content``."""

    content: str


class CallableLLM:
    """Wrap a plain ``prompt: str -> str`` callable as an LLM handle.

    Accepts the call styles LLM clients (and ``neo4j_graphrag``) use: a
    prompt string, optionally with ``system_instruction``/
    ``message_history``, or a list of messages (dicts or objects with
    ``content``); messages are flattened into a single prompt.

    Args:
        fn: Function that sends a prompt to an LLM and returns its text answer.
        model_name: Informative name only.
    """

    def __init__(self, fn: Callable[[str], str], model_name: str = "callable") -> None:
        if not callable(fn):
            raise TypeError(f"CallableLLM expects a callable, got {type(fn).__name__}")
        self.fn = fn
        self.model_name = model_name

    @staticmethod
    def _to_prompt(
        input: Any,
        message_history: Any = None,
        system_instruction: Optional[str] = None,
    ) -> str:
        parts: List[str] = []
        if system_instruction:
            parts.append(system_instruction)
        history = getattr(message_history, "messages", message_history) or []
        for msg in list(history) + (input if isinstance(input, list) else []):
            parts.append(msg["content"] if isinstance(msg, dict) else msg.content)
        if isinstance(input, str):
            parts.append(input)
        return "\n\n".join(parts)

    def invoke(
        self,
        input: Any,
        message_history: Any = None,
        system_instruction: Optional[str] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        answer = self.fn(self._to_prompt(input, message_history, system_instruction))
        if not isinstance(answer, str):
            raise TypeError(
                f"The LLM callable must return a str, got {type(answer).__name__}"
            )
        return LLMResponse(content=answer)

    async def ainvoke(
        self,
        input: Any,
        message_history: Any = None,
        system_instruction: Optional[str] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        return await asyncio.to_thread(
            self.invoke, input, message_history, system_instruction, **kwargs
        )


def extract_cypher(text: str) -> str:
    """Return the Cypher in an LLM answer (inside a code fence if there is one)."""
    match = re.search(r"```(?:[a-zA-Z]+\n)?(.*?)```", text, re.DOTALL)
    return (match.group(1) if match else text).strip()


def is_read_only_cypher(cypher: str) -> bool:
    """Whether *cypher* has no clause that could modify the graph (or call a
    procedure). String literals and backtick-quoted names are ignored."""
    return not _WRITE_CLAUSE.search(_STRING_LITERAL.sub("''", cypher))


def format_schema(
    node_props: Dict[str, Sequence[Tuple[str, str]]],
    rel_props: Dict[str, Sequence[Tuple[str, str]]],
    relationships: Iterable[Tuple[str, str, str]],
) -> str:
    """Render a graph schema as the text given to the LLM (same layout as
    ``neo4j_graphrag.schema.get_schema``).

    Args:
        node_props: ``{label: [(property, TYPE), ...]}``.
        rel_props: ``{rel_type: [(property, TYPE), ...]}``.
        relationships: ``(start_label, rel_type, end_label)`` patterns.
    """

    def _props(d: Dict[str, Sequence[Tuple[str, str]]]) -> str:
        return "\n".join(
            f"{name} {{{', '.join(f'{p}: {t}' for p, t in props)}}}"
            for name, props in d.items()
        )

    rels = "\n".join(f"(:{s})-[:{t}]->(:{e})" for s, t, e in relationships)
    return "\n".join(
        [
            "Node properties:",
            _props(node_props),
            "Relationship properties:",
            _props(rel_props),
            "The relationships:",
            rels,
        ]
    )


def _type_name(raw: Optional[str]) -> str:
    if not raw:
        return "STRING"
    # Neo4j 5 reports e.g. "String"/"StringArray"; newer versions may report
    # Cypher type names such as "STRING NOT NULL" or "LIST<STRING>".
    raw = raw.replace(" NOT NULL", "")
    if raw.endswith("Array") or raw.startswith("LIST"):
        return "LIST"
    return _TYPE_NAMES.get(raw, raw.upper())


def _add_prop(props: Dict[str, List[Tuple[str, str]]], owner: str, name: Any, type_name: str) -> None:
    entry = props.setdefault(owner, [])
    if name is None or str(name).startswith("_"):
        return
    if all(p != name for p, _ in entry):
        entry.append((name, type_name))


def _sorted(d: Dict[str, List[Tuple[str, str]]]) -> Dict[str, List[Tuple[str, str]]]:
    return {k: d[k] for k in sorted(d)}


@dataclass
class Text2CypherResult:
    """Answer to a natural-language question.

    Attributes:
        cypher: The Cypher query generated by the LLM (and executed).
        records: Query results, one ``dict`` per row, shaped like
            ``graph.query(cypher)``'s output.
        metadata: Extra metadata (``cypher``, and the retriever's own on Neo4j).
    """

    cypher: str
    records: List[Dict[str, Any]]
    metadata: Dict[str, Any] = field(default_factory=dict)


def _is_neo4j_graph(graph: Any) -> bool:
    try:
        from .neo4j_graph import Neo4jGraph
    except ImportError:  # pragma: no cover - neo4j driver not installed
        return False
    return isinstance(graph, Neo4jGraph)


class Text2Cypher:
    """Ask natural-language questions to a cvcdocdb graph, whatever its backend.

    Unless *schema* is given, the graph schema shown to the LLM is
    introspected from the graph (on Neo4j with built-in ``db.schema.*``
    procedures, so the APOC plugin is not needed). Properties starting with
    ``_`` and cvcdocdb bookkeeping attributes are left out of it.

    Args:
        graph: The graph to query: a ``Neo4jGraph``, a ``NetworkXGraph``, or
            any backend whose ``query()`` accepts a Cypher string.
        llm: The LLM handle — a ``prompt: str -> str`` callable, or an object
            with ``invoke(prompt)`` returning an answer with ``.content``.
        schema: Schema text for the prompt; introspected when omitted.
        examples: Optional example question/Cypher pairs for the prompt.
        custom_prompt: Optional prompt template replacing
            :data:`DEFAULT_PROMPT`, with ``{schema}``, ``{examples}`` and
            ``{query_text}`` placeholders.
        database: Neo4j only — database to query; defaults to the graph's.

    Attributes:
        retriever: On Neo4j, the underlying ``neo4j_graphrag``
            ``Text2CypherRetriever`` (e.g. to plug it into a ``GraphRAG``
            pipeline); ``None`` on other backends.

    Raises:
        TypeError: If *graph* has no ``query()`` method, or *llm* is neither
            a callable nor an object with ``invoke``.
        ImportError: On a ``Neo4jGraph``, if ``neo4j-graphrag`` is missing.
    """

    def __init__(
        self,
        graph: Any,
        llm: Any,
        *,
        schema: Optional[str] = None,
        examples: Optional[List[str]] = None,
        custom_prompt: Optional[str] = None,
        database: Optional[str] = None,
    ) -> None:
        if not callable(getattr(graph, "query", None)):
            raise TypeError(
                "Text2Cypher needs a graph with a Cypher-capable query() "
                f"method, got {type(graph).__name__}"
            )
        if not callable(getattr(llm, "invoke", None)):
            if not callable(llm):
                raise TypeError(
                    "llm must be a callable prompt -> str or an object with an "
                    f"'invoke' method, got {type(llm).__name__}"
                )
            llm = CallableLLM(llm)

        self.graph = graph
        self.llm = llm
        self.examples = examples
        self.custom_prompt = custom_prompt
        self.retriever: Any = None

        if _is_neo4j_graph(graph):
            try:
                from neo4j_graphrag.retrievers import Text2CypherRetriever
            except ImportError as exc:
                raise ImportError(_INSTALL_HINT) from exc
            self.database = database if database is not None else self._neo4j_database()
            self.schema = schema if schema is not None else self._neo4j_schema()
            self.retriever = Text2CypherRetriever(
                driver=graph._driver,
                llm=llm,
                neo4j_schema=self.schema,
                examples=examples,
                custom_prompt=custom_prompt,
                neo4j_database=self.database,
            )
        else:
            self.database = database
            self.schema = schema if schema is not None else self._generic_schema()

    def query(self, question: str) -> Text2CypherResult:
        """Translate *question* to Cypher with the LLM, run it, return the rows.

        Raises:
            Text2CypherError: If the generated Cypher is invalid or not
                read-only (nothing is executed in that case).
        """
        if self.retriever is not None:
            return self._query_neo4j(question)
        return self._query_generic(question)

    # ------------------------------------------------------------------
    # Generic backend (graph.query(cypher))
    # ------------------------------------------------------------------

    def _prompt(self, question: str) -> str:
        return (self.custom_prompt or DEFAULT_PROMPT).format(
            schema=self.schema,
            examples="\n".join(self.examples or []),
            query_text=question,
        )

    def _query_generic(self, question: str) -> Text2CypherResult:
        cypher = extract_cypher(self.llm.invoke(self._prompt(question)).content)
        if not is_read_only_cypher(cypher):
            raise Text2CypherError(f"Refusing to execute non-read-only Cypher: {cypher}")
        try:
            records = self.graph.query(cypher)
        except Exception as exc:
            raise Text2CypherError(f"Failed to run generated Cypher {cypher!r}: {exc}") from exc
        return Text2CypherResult(cypher=cypher, records=records, metadata={"cypher": cypher})

    def _generic_schema(self) -> str:
        node_props: Dict[str, List[Tuple[str, str]]] = {}
        labels: Dict[Any, str] = {}
        for nid in self.graph.get_node_ids():
            attrs = self.graph.get_node_attrs(nid) or {}
            label = labels[nid] = attrs.get("main_label", "Node")
            node_props.setdefault(label, [])
            for name, value in attrs.items():
                if name not in _INTERNAL_PROPERTIES:
                    _add_prop(node_props, label, name, _type_name(type(value).__name__))

        rel_props: Dict[str, List[Tuple[str, str]]] = {}
        patterns = set()
        for u, v, rel_type in self.graph.get_edges():
            patterns.add((labels.get(u, "Node"), rel_type, labels.get(v, "Node")))
            rel_props.setdefault(rel_type, [])
            for name, value in (self.graph.get_edge_attrs(u, v, rel_type) or {}).items():
                _add_prop(rel_props, rel_type, name, _type_name(type(value).__name__))
        rel_props = {k: v for k, v in rel_props.items() if v}
        return format_schema(_sorted(node_props), _sorted(rel_props), sorted(patterns))

    # ------------------------------------------------------------------
    # Neo4j backend (neo4j_graphrag Text2CypherRetriever)
    # ------------------------------------------------------------------

    def _query_neo4j(self, question: str) -> Text2CypherResult:
        from neo4j_graphrag.exceptions import Text2CypherRetrievalError

        from .neo4j_graph import _convert_neo4j_value

        try:
            raw = self.retriever.get_search_results(question)
        except Text2CypherRetrievalError as exc:
            raise Text2CypherError(str(exc)) from exc
        metadata = dict(raw.metadata or {})
        records = [
            {key: _convert_neo4j_value(value) for key, value in record.items()}
            for record in raw.records
        ]
        return Text2CypherResult(
            cypher=metadata.get("cypher", "").strip(), records=records, metadata=metadata
        )

    def _run(self, cypher: str) -> List[Any]:
        records, _, _ = self.graph._driver.execute_query(cypher, database_=self.database)
        return records

    def _neo4j_database(self) -> Optional[str]:
        # The graph's session is bound to the database it was opened with;
        # ask it (db.info() is built in) instead of relying on driver internals.
        try:
            return self.graph._session.run("CALL db.info() YIELD name").single()["name"]
        except Exception:
            return None

    def _neo4j_schema(self) -> str:
        node_props: Dict[str, List[Tuple[str, str]]] = {}
        for rec in self._run(
            "CALL db.schema.nodeTypeProperties() "
            "YIELD nodeLabels, propertyName, propertyTypes "
            "RETURN nodeLabels, propertyName, propertyTypes"
        ):
            for label in rec["nodeLabels"]:
                _add_prop(node_props, label, rec["propertyName"],
                          _type_name((rec["propertyTypes"] or [None])[0]))

        rel_props: Dict[str, List[Tuple[str, str]]] = {}
        for rec in self._run(
            "CALL db.schema.relTypeProperties() "
            "YIELD relType, propertyName, propertyTypes "
            "RETURN relType, propertyName, propertyTypes"
        ):
            rel_type = rec["relType"].lstrip(":").strip("`")
            _add_prop(rel_props, rel_type, rec["propertyName"],
                      _type_name((rec["propertyTypes"] or [None])[0]))
        rel_props = {k: v for k, v in rel_props.items() if v}

        relationships = [
            (rec["start"], rec["type"], rec["end"])
            for rec in self._run(
                "MATCH (a)-[r]->(b) "
                "WITH DISTINCT labels(a) AS la, type(r) AS t, labels(b) AS lb "
                "UNWIND la AS s UNWIND lb AS e "
                "RETURN DISTINCT s AS start, t AS type, e AS end "
                "ORDER BY start, type, end"
            )
        ]
        return format_schema(_sorted(node_props), _sorted(rel_props), relationships)
