"""JenaGraph — Apache Jena (SPARQL) backend for the DRM graph model.

:class:`JenaGraph` stores the graph as RDF 1.2 in a SPARQL store such as
`Apache Jena Fuseki <https://jena.apache.org/documentation/fuseki2/>`_ (see
:mod:`cvcdocdb.jena_rdf` for the RDF layout), with the same API and the same
behaviour as every other backend.

How it works:

- It reuses :class:`~cvcdocdb.networkx_graph.NetworkXGraph`'s logic, which
  behaves exactly like ``Neo4jGraph`` (the shared propagation scenarios
  check it), on an in-memory copy of the graph.
- Every mutating call, or a whole :meth:`batch`, is sent to the server as a
  **single atomic SPARQL Update request** with only what changed. If the
  call fails, nothing is sent and the in-memory copy is rolled back.
- Writes are protected by a version stamp. Before each write, the copy is
  refreshed if another client changed the graph. If another client writes
  in the middle of a call or batch, nothing is applied, the copy is
  reloaded, and :class:`ConcurrentModificationError` is raised.
- ``query()`` with a SPARQL string runs it on the server. Dict filters and
  Cypher strings run on the in-memory copy, as on ``NetworkXGraph``.

The whole graph is held in memory, so the graph has to fit in RAM.
"""

from __future__ import annotations

import re
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Tuple

import networkx as nx

from .jena_rdf import DEFAULT_NAMESPACE, RdfMapping, Triple, _parse_term, term_from_binding
from .networkx_graph import NetworkXGraph
from .sparql_client import DEFAULT_TIMEOUT, SparqlClient, SparqlError

try:  # mateixa excepció que espera NetworkXGraph._guarded_write
    from filelock import Timeout as _LockTimeout
except ImportError:  # pragma: no cover - filelock és una dependència
    _LockTimeout = TimeoutError

__all__ = ["ConcurrentModificationError", "JenaGraph", "SparqlError", "is_sparql", "is_sparql_update"]


class ConcurrentModificationError(RuntimeError):
    """Another client changed the graph during this call or batch; nothing
    was applied and the in-memory copy has been reloaded. Retry the call."""


# ---------------------------------------------------------------------------
# SPARQL detection
# ---------------------------------------------------------------------------

_COMMENT = re.compile(r"#[^\n]*")
_PROLOGUE = re.compile(r"\s*(?:PREFIX\s+[^\s:]*:\s*<[^>]*>|BASE\s*<[^>]*>)", re.IGNORECASE)
_QUERY_FORMS = ("SELECT", "ASK", "CONSTRUCT", "DESCRIBE")
# Paraules clau d'actualització SPARQL i què les distingeix de Cypher.
_UPDATE_FORMS = {
    "INSERT": r"",
    "DELETE": r"\s*(?:DATA\b|WHERE\b|\{)",
    "LOAD": r"\s*(?:SILENT\b|<)",
    "CLEAR": r"",
    "DROP": r"\s*(?:SILENT\b|GRAPH\b|DEFAULT\b|NAMED\b|ALL\b)",
    "CREATE": r"\s*(?:SILENT\b|GRAPH\b)",
    "ADD": r"",
    "MOVE": r"",
    "COPY": r"",
    "WITH": r"\s*<",
}


def _body(text: str) -> Tuple[str, bool]:
    """(text after comments and the PREFIX/BASE prologue, whether it had a prologue)."""
    text = _COMMENT.sub(" ", text)
    had_prologue = False
    while True:
        match = _PROLOGUE.match(text)
        if not match:
            return text.lstrip(), had_prologue
        had_prologue = True
        text = text[match.end():]


def _first_word(text: str) -> str:
    match = re.match(r"([A-Za-z]+)", text)
    return match.group(1).upper() if match else ""


def is_sparql_update(text: str) -> bool:
    """Whether *text* is a SPARQL Update request."""
    body, _ = _body(text)
    word = _first_word(body)
    follow = _UPDATE_FORMS.get(word)
    return follow is not None and re.match(follow, body[len(word):], re.IGNORECASE) is not None


def is_sparql(text: str) -> bool:
    """Whether *text* is SPARQL (query or update) rather than Cypher."""
    body, had_prologue = _body(text)
    return had_prologue or _first_word(body) in _QUERY_FORMS or is_sparql_update(text)


# ---------------------------------------------------------------------------
# In-process write lock (same interface NetworkXGraph expects from FileLock)
# ---------------------------------------------------------------------------


class _ThreadWriteLock:
    """Reentrant lock whose ``is_locked`` is true only for the owning thread.

    ``NetworkXGraph._guarded_write`` uses ``is_locked`` to tell the
    outermost call apart from nested ones; concurrency with other clients
    is handled by the server-side version stamp instead.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._owner: Optional[int] = None
        self._depth = 0

    @property
    def is_locked(self) -> bool:
        return self._owner == threading.get_ident()

    def acquire(self, timeout: float = -1) -> None:
        if not self._lock.acquire(timeout=-1 if timeout is None or timeout < 0 else timeout):
            raise _LockTimeout("JenaGraph write lock")
        self._owner = threading.get_ident()
        self._depth += 1

    def release(self) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
        self._lock.release()


# ---------------------------------------------------------------------------
# JenaGraph
# ---------------------------------------------------------------------------


class JenaGraph(NetworkXGraph):
    """Graph store on Apache Jena Fuseki (or another SPARQL 1.2 store).

    Args:
        url: Dataset URL, e.g. ``http://localhost:3030/ds``. Queries go to
            ``<url>/query`` and updates to ``<url>/update`` unless
            *query_url*/*update_url* are given.
        user: Optional HTTP basic-auth user.
        password: Optional HTTP basic-auth password.
        namespace: IRI prefix of everything this graph writes (default
            ``urn:cvcdocdb:``). Triples outside it are never touched, so
            several graphs can share a dataset with different namespaces.
        graph_iri: Store the data in this named graph instead of the
            default graph.
        query_url: Explicit SPARQL query endpoint.
        update_url: Explicit SPARQL update endpoint.
        timeout: Seconds to wait for each HTTP request.
        lock_timeout: Seconds a mutating call waits for another thread of
            this process using the same instance (``None``: forever).

    Raises:
        SparqlError: If the server can't be reached or rejects a request.
    """

    def __init__(
        self,
        url: str,
        user: Optional[str] = None,
        password: Optional[str] = None,
        *,
        namespace: str = DEFAULT_NAMESPACE,
        graph_iri: Optional[str] = None,
        query_url: Optional[str] = None,
        update_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        lock_timeout: Optional[float] = None,
    ) -> None:
        base = url.rstrip("/")
        self._client = SparqlClient(query_url or f"{base}/query", update_url or f"{base}/update",
                                    user, password, timeout)
        self._mapping = RdfMapping(namespace)
        if graph_iri is not None and any(ch in graph_iri for ch in '<>" {}|\\^`'):
            raise ValueError(f"Invalid graph IRI {graph_iri!r}")
        self._graph_iri = graph_iri
        self._synced: Dict[str, FrozenSet[Triple]] = {}
        self._synced_version: Optional[str] = None
        self._loaded = False
        # NetworkXGraph.__init__ carrega l'estat (via el _load_state d'aquí).
        super().__init__(persistence_path=f"sparql+{base}", lock_timeout=lock_timeout)
        self._file_lock = _ThreadWriteLock()

    # -- SPARQL helpers ----------------------------------------------------

    def _in_graph(self, pattern: str) -> str:
        return f"GRAPH <{self._graph_iri}> {{ {pattern} }}" if self._graph_iri else pattern

    def _namespace_filter(self, var: str = "?s") -> str:
        ns = self._mapping.namespace.replace("\\", "\\\\").replace('"', '\\"')
        return f'FILTER(STRSTARTS(STR({var}), "{ns}"))'

    def _remote_version(self) -> Optional[str]:
        m = self._mapping
        rows = self._client.select(
            f"SELECT ?v WHERE {{ {self._in_graph(f'{m.meta_sync} {m.p_sync_version} ?v')} }}"
        )
        return rows[0]["v"]["value"] if rows else None

    # -- persistence (NetworkXGraph hooks) ---------------------------------

    def _load_state(self) -> None:
        """Refresh the in-memory copy if the server's version changed."""
        version = self._remote_version()
        if self._loaded and version == self._synced_version:
            return
        rows = self._client.select(
            f"SELECT ?s ?p ?o WHERE {{ {self._in_graph('?s ?p ?o ' + self._namespace_filter())} }}"
        )
        parsed = [(term_from_binding(r["s"]), term_from_binding(r["p"]), term_from_binding(r["o"])) for r in rows]
        node_attrs, edge_attrs, meta = self._mapping.state_from_triples(parsed)
        self._set_state(node_attrs, edge_attrs, meta)
        self._synced = self._current_subjects()
        self._synced_version = version
        self._loaded = True

    def _set_state(self, node_attrs: Dict[int, Dict[str, Any]],
                   edge_attrs: Dict[Tuple[int, int, str], Dict[str, Any]], meta: Dict[str, Any]) -> None:
        self._graph = nx.MultiDiGraph()
        self._node_attrs = node_attrs
        self._edge_attrs = edge_attrs
        self._node_counter = int(meta.get("node_counter", 0))
        self._version = meta.get("version", 0.0)
        self._fk_index = {}
        self._vector_indexes = {}
        self._vector_index_meta = {}
        for node_id, attrs in node_attrs.items():
            self._graph.add_node(node_id, **{k: v for k, v in attrs.items() if k not in ("pk", "main_label", "labels")})
        for src, dst, rel_type in edge_attrs:
            self._graph.add_edge(src, dst, key=rel_type, rel_type=rel_type)
            self._add_to_fk_index(src, dst, rel_type)
        self._rebuild_indexes()

    def _current_subjects(self) -> Dict[str, FrozenSet[Triple]]:
        meta = {"node_counter": self._node_counter, "version": self._version}
        return self._mapping.subjects(self._mapping.state_to_triples(self._node_attrs, self._edge_attrs, meta))

    def _save_state(self) -> None:
        """Send what changed since the last sync as one atomic update."""
        current = self._current_subjects()
        changed = sorted(s for s in set(current) | set(self._synced) if current.get(s) != self._synced.get(s))
        if not changed:
            return
        m = self._mapping
        token = f'"{uuid.uuid4().hex}"'
        new_version = uuid.uuid4().hex
        guard = self._in_graph(f"{m.meta_sync} {m.p_commit} {token}")
        if self._synced_version is None:
            expected = f"FILTER NOT EXISTS {{ {self._in_graph(f'{m.meta_sync} {m.p_sync_version} ?any')} }}"
        else:
            expected = self._in_graph(f'{m.meta_sync} {m.p_sync_version} "{self._synced_version}"')
        operations = [f"INSERT {{ {guard} }} WHERE {{ {expected} }}"]
        for subject in changed:
            if subject in self._synced:
                pattern = self._in_graph(f"{subject} ?p ?o")
                operations.append(f"DELETE {{ {pattern} }} WHERE {{ {guard} . {pattern} }}")
            if subject in current:
                triples = " ".join(f"{s} {p} {o} ." for s, p, o in sorted(current[subject]))
                operations.append(f"INSERT {{ {self._in_graph(triples)} }} WHERE {{ {guard} }}")
        old = self._in_graph(f"{m.meta_sync} {m.p_sync_version} ?old")
        stamp = self._in_graph(f'{m.meta_sync} {m.p_sync_version} "{new_version}"')
        operations.append(
            f"DELETE {{ {old} . {guard} }} INSERT {{ {stamp} }} WHERE {{ {guard} OPTIONAL {{ {old} }} }}"
        )
        self._client.update(" ;\n".join(operations))
        if self._remote_version() != new_version:
            self._discard_unsaved_changes()
            raise ConcurrentModificationError(
                "The graph was changed by another client during this write; nothing was applied. "
                "The local copy has been reloaded: retry the operation."
            )
        self._synced = current
        self._synced_version = new_version

    def _discard_unsaved_changes(self) -> None:
        self._loaded = False
        self._load_state()
        self._node_pks = {nid: pk for nid, pk in getattr(self, "_node_pks", {}).items() if nid in self._node_attrs}

    # -- public API --------------------------------------------------------

    def query(
        self,
        filter_dict: Optional[Any] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[Tuple[str, int]] = None,
        limit_val: Optional[int] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Like :meth:`NetworkXGraph.query`, plus SPARQL.

        A SPARQL string (``SELECT``/``ASK``/``CONSTRUCT``/``DESCRIBE``, with
        an optional ``PREFIX``/``BASE`` prologue) runs on the server and sees
        the committed data. *params* are bound with a trailing ``VALUES``
        clause (``{"name": value}`` binds ``?name``). Rows map variables to
        Python values (IRIs as strings). ``ASK`` returns ``[{"ask": bool}]``;
        ``CONSTRUCT``/``DESCRIBE`` return one ``{"subject", "predicate",
        "object"}`` row per triple.

        SPARQL Update is refused (``ValueError``): writes go through the
        ``GraphStore`` API so the propagation policy is applied. Dict
        filters and Cypher run on the in-memory copy, as on NetworkXGraph.
        """
        if isinstance(filter_dict, str) and is_sparql(filter_dict):
            if is_sparql_update(filter_dict):
                raise ValueError(
                    "SPARQL Update is not allowed through query(): use insertNode/insertRelation/"
                    "deleteNode so the propagation policy is applied."
                )
            return self._sparql_query(filter_dict, params or {})
        return super().query(filter_dict, projection, sort, limit_val, params)

    def _sparql_query(self, text: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        if params:
            names = " ".join(f"?{name}" for name in params)
            values = " ".join(self._mapping.encode_value(v) for v in params.values())
            text = f"{text}\nVALUES ({names}) {{ ({values}) }}"
        body, _ = _body(text)
        form = _first_word(body)
        if form == "ASK":
            return [{"ask": self._client.ask(text)}]
        if form in ("CONSTRUCT", "DESCRIBE"):
            rows = []
            for line in self._client.graph(text).splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                s, rest = _parse_term(line)
                p, rest = _parse_term(rest)
                o, _ = _parse_term(rest)
                rows.append({"subject": self._mapping.decode_term(s) if s[0] != "triple" else s,
                             "predicate": self._mapping.decode_term(p),
                             "object": self._mapping.decode_term(o) if o[0] in ("uri", "literal") else o})
            return rows
        return [
            {var: self._decode_binding(value) for var, value in row.items()}
            for row in self._client.select(text)
        ]

    def _decode_binding(self, binding: Dict[str, Any]) -> Any:
        term = term_from_binding(binding)
        if term[0] in ("uri", "literal"):
            return self._mapping.decode_term(term)
        return binding["value"]

    def clear(self) -> None:
        """Delete everything this graph wrote (its namespace) on the server.
        Triples outside the namespace are left alone."""
        with self._guarded_write(save=False):
            pattern = self._in_graph("?s ?p ?o")
            self._client.update(
                f"DELETE {{ {pattern} }} WHERE {{ {self._in_graph('?s ?p ?o ' + self._namespace_filter())} }}"
            )
            self._loaded = False
            self._load_state()
            self._node_pks = {}

    def enable_vector_index(self, *args: Any, **kwargs: Any) -> None:
        """Not supported on Jena (as on Neo4j)."""
        raise NotImplementedError("JenaGraph does not support vector indexes.")

    def query_vector_index(self, *args: Any, **kwargs: Any) -> List[Tuple[int, float]]:
        """Not supported on Jena (as on Neo4j)."""
        raise NotImplementedError("JenaGraph does not support vector indexes.")

    def list_vector_indexes(self) -> List[Dict[str, Any]]:
        """JenaGraph has no vector indexes — always empty."""
        return []

    def close(self) -> None:
        super().close()
        self._synced = {}
        self._synced_version = None
        self._loaded = False

    @contextmanager
    def batch(self, write: bool = True) -> Iterator[None]:
        """Group calls into ONE atomic SPARQL Update, sent when the block
        ends. If the block fails, nothing is sent and the in-memory copy is
        rolled back. Reads inside the block see its own writes (SPARQL
        strings excepted: they run on the server)."""
        with super().batch(write=write):
            yield
