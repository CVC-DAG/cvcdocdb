"""Read-only, backend-agnostic data layer of the graph viewer.

:class:`GraphViewerData` answers the questions the viewer's page asks — which
labels and relation types exist, which nodes match some filters, what a node
holds and what it is connected to — using **only the public**
:class:`~cvcdocdb.graph_store.GraphStore` API:

* dict filters through :meth:`GraphStore.query` (``main_label``, ``$in``,
  ``$ne``, ``$gt``..., ``$exists``, ``$regex``), which every backend
  translates itself (bound parameters on Neo4j/Memgraph, Python matching on
  NetworkX/Jena);
* a few **fixed** read-only Cypher statements with ``$parameters`` for counts,
  node detail and neighbours (Neo4j/Memgraph run them natively; NetworkXGraph
  and JenaGraph run them with :mod:`cvcdocdb.nx_cypher`).

User input never becomes query text: labels, property names and relation
types are checked against an identifier pattern and passed as filter keys or
parameters, values are always bound, and "contains" uses an escaped pattern.
Every result is capped (page size, scan size, neighbours), so a large remote
graph can't be pulled whole by one request.

Calls are serialised with a lock: the server is multi-threaded and a
Neo4j session isn't thread-safe.
"""

from __future__ import annotations

import math
import re
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..graph_store import GraphStore

__all__ = [
    "GraphViewerData",
    "InvalidRequest",
    "NodeNotFound",
    "FILTER_OPERATORS",
]

#: Default number of results per page, and the most a caller can ask for.
DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_PAGE_SIZE = 200
#: Most nodes one search reads from the store (paging/free text work on these).
DEFAULT_MAX_SCAN = 5000
#: Default and maximum number of relations returned for one node.
DEFAULT_NEIGHBOURS = 50
DEFAULT_MAX_NEIGHBOURS = 500
#: Default cap on the relations :meth:`GraphViewerData.relations_among` returns.
DEFAULT_MAX_RELATIONS = 2000
#: Most node ids one :meth:`GraphViewerData.relations_among` call accepts.
MAX_RELATION_IDS = 1000
#: Input limits.
MAX_FILTERS = 20
MAX_TEXT_LENGTH = 200
MAX_IDENTIFIER_LENGTH = 128
#: Size limits of the values sent to the page.
SUMMARY_STRING_LENGTH = 200
DETAIL_STRING_LENGTH = 20000
MAX_LIST_ITEMS = 100

#: Keys NetworkXGraph keeps next to the real properties of a node.
_INTERNAL_KEYS = ("pk", "main_label", "labels")
#: Properties tried, in order, to give a node a readable caption.
_CAPTION_KEYS = ("name", "title", "label", "nom", "titol", "caption", "id", "code", "uri")

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Filter operators accepted by :meth:`GraphViewerData.search`.
FILTER_OPERATORS = ("eq", "ne", "contains", "gt", "gte", "lt", "lte", "exists")
_COMPARISON_OPS = {"ne": "$ne", "gt": "$gt", "gte": "$gte", "lt": "$lt", "lte": "$lte"}
_DIRECTIONS = ("both", "out", "in")

_COUNT_LABELS = "MATCH (n) RETURN labels(n) AS labels, count(*) AS c"
_COUNT_RELATIONS = "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS c"
_NODE_DETAIL = "MATCH (n) WHERE id(n) = $id RETURN labels(n) AS labels, properties(n) AS props"
_RELATIONS_AMONG = (
    "MATCH (n)-[r]->(m) WHERE id(n) IN $ids AND id(m) IN $ids "
    "RETURN id(n) AS source, type(r) AS type, id(m) AS target, properties(r) AS rprops LIMIT $limit"
)
_NEIGHBOURS = {
    "out": "MATCH (n)-[r]->(m) WHERE id(n) = $id{type_clause} "
           "RETURN type(r) AS type, properties(r) AS rprops, id(m) AS mid, "
           "labels(m) AS labels, properties(m) AS props LIMIT $limit",
    "in": "MATCH (n)<-[r]-(m) WHERE id(n) = $id{type_clause} "
          "RETURN type(r) AS type, properties(r) AS rprops, id(m) AS mid, "
          "labels(m) AS labels, properties(m) AS props LIMIT $limit",
}


class InvalidRequest(ValueError):
    """A viewer request had an invalid or out-of-range parameter."""


class NodeNotFound(LookupError):
    """No node has the requested id."""


# ----------------------------------------------------------------------
# Validation helpers
# ----------------------------------------------------------------------


def _identifier(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER_LENGTH \
            or not _IDENTIFIER_RE.match(value):
        raise InvalidRequest(f"Invalid {what}: {value!r} (letters, digits and '_' only)")
    return value


def _int_in_range(value: Any, what: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRequest(f"{what} must be an integer")
    if value < minimum:
        raise InvalidRequest(f"{what} must be >= {minimum}")
    return value


def _node_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRequest("Node id must be an integer")
    return value


def _coerce_scalar(raw: str) -> List[Any]:
    """Candidate typed values of a string from a URL: the string itself, plus
    the number or boolean it spells, so ``"30"`` also matches ``30``."""
    candidates: List[Any] = [raw]
    text = raw.strip()
    lowered = text.lower()
    if lowered in ("true", "false"):
        candidates.append(lowered == "true")
    else:
        try:
            candidates.append(int(text))
        except ValueError:
            try:
                number = float(text)
            except ValueError:
                pass
            else:
                if math.isfinite(number):
                    candidates.append(number)
    return candidates


def _comparable(raw: str) -> Any:
    """Typed value for an ordering comparison (a number when it is one)."""
    candidates = _coerce_scalar(raw)
    numeric = [c for c in candidates if isinstance(c, (int, float)) and not isinstance(c, bool)]
    return numeric[0] if numeric else raw


# ----------------------------------------------------------------------
# Value shaping
# ----------------------------------------------------------------------


def _jsonable(value: Any, max_string: int, depth: int = 0) -> Any:
    """A JSON-safe, size-limited copy of a property value."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        return value if len(value) <= max_string else value[:max_string] + "…"
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if depth >= 4:
        return _jsonable(str(value), max_string, depth)
    if isinstance(value, dict):
        return {str(k): _jsonable(v, max_string, depth + 1) for k, v in list(value.items())[:MAX_LIST_ITEMS]}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        if isinstance(value, (set, frozenset)):
            items = sorted(items, key=repr)
        shaped = [_jsonable(v, max_string, depth + 1) for v in items[:MAX_LIST_ITEMS]]
        if len(items) > MAX_LIST_ITEMS:
            shaped.append(f"… {len(items) - MAX_LIST_ITEMS} more")
        return shaped
    tolist = getattr(value, "tolist", None)  # numpy arrays/scalars
    if callable(tolist):
        try:
            return _jsonable(tolist(), max_string, depth + 1)
        except Exception:  # noqa: BLE001 - fall back to the text form
            pass
    return _jsonable(str(value), max_string, depth)


def _split_properties(raw: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """``(pk, properties)`` without the backend's bookkeeping keys."""
    raw = dict(raw or {})
    pk = raw.get("pk") if isinstance(raw.get("pk"), dict) else None
    props = {k: v for k, v in raw.items() if k not in _INTERNAL_KEYS}
    return pk, props


def _caption(node_id: Any, pk: Optional[Dict[str, Any]], props: Dict[str, Any]) -> str:
    for key in _CAPTION_KEYS:
        value = props.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value).strip():
            return str(value)[:80]
    if pk:
        return ", ".join(f"{v}" for v in pk.values())[:80]
    return f"#{node_id}"


def _text_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str):
                yield item


# ----------------------------------------------------------------------
# Data layer
# ----------------------------------------------------------------------


class GraphViewerData:
    """Read-only browsing operations over any :class:`GraphStore`.

    Args:
        store: An open graph store (``NetworkXGraph``, ``Neo4jGraph``,
            ``MemgraphGraph``, ``JenaGraph``...). It is only read.
        max_page_size: Most results :meth:`search` returns per page.
        max_scan: Most nodes one :meth:`search` reads from the store; the
            total it reports is a lower bound past this.
        max_neighbours: Most relations :meth:`neighbours` returns.
        max_relations: Most relations :meth:`relations_among` returns.
    """

    def __init__(
        self,
        store: GraphStore,
        max_page_size: int = DEFAULT_MAX_PAGE_SIZE,
        max_scan: int = DEFAULT_MAX_SCAN,
        max_neighbours: int = DEFAULT_MAX_NEIGHBOURS,
        max_relations: int = DEFAULT_MAX_RELATIONS,
    ) -> None:
        for name, value in (("max_page_size", max_page_size), ("max_scan", max_scan),
                            ("max_neighbours", max_neighbours), ("max_relations", max_relations)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.store = store
        self.max_page_size = max_page_size
        self.max_scan = max_scan
        self.max_neighbours = max_neighbours
        self.max_relations = max_relations
        self._lock = threading.Lock()

    # -- store access ----------------------------------------------------

    def _cypher(self, text: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        with self._lock:
            return self.store.query(text, params=params or {})

    def _find(self, filter_dict: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        with self._lock:
            try:
                return self.store.query(filter_dict, limit_val=limit)
            except TypeError as exc:
                # In-memory backends compare Python values: "age > 'abc'" on
                # integers can't be evaluated. That's a bad request, not a crash.
                raise InvalidRequest(f"Filter value can't be compared with the stored values: {exc}") from None

    # -- shaping -----------------------------------------------------------

    def _summary_of(self, node_id: Any, labels: Sequence[str], main_label: Optional[str],
                    raw_props: Optional[Dict[str, Any]], max_string: int) -> Dict[str, Any]:
        pk, props = _split_properties(raw_props)
        raw_labels = (raw_props or {}).get("labels")
        if not labels and isinstance(raw_labels, list):
            labels = raw_labels
        labels = [str(label) for label in labels] or ([main_label] if main_label else [])
        main = main_label or (labels[0] if labels else "Node")
        return {
            "id": node_id,
            "main_label": main,
            "labels": labels,
            "pk": _jsonable(pk, max_string) if pk else None,
            "caption": _caption(node_id, pk, props),
            "properties": {k: _jsonable(v, max_string) for k, v in sorted(props.items())},
        }

    # -- operations ----------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Node counts per main label and relation counts per type.

        Returns:
            A dict with ``backend``, ``labels`` (``[{"label", "count"}]``),
            ``relation_types`` (``[{"type", "count"}]``), ``node_count`` and
            ``relation_count``; labels and types sorted by name.
        """
        label_counts: Dict[str, int] = {}
        for row in self._cypher(_COUNT_LABELS):
            labels = row.get("labels") or ["Node"]
            label = str(labels[0])
            label_counts[label] = label_counts.get(label, 0) + int(row.get("c") or 0)
        type_counts: Dict[str, int] = {}
        for row in self._cypher(_COUNT_RELATIONS):
            rel_type = str(row.get("type"))
            type_counts[rel_type] = type_counts.get(rel_type, 0) + int(row.get("c") or 0)
        return {
            "backend": type(self.store).__name__,
            "labels": [{"label": k, "count": v} for k, v in sorted(label_counts.items())],
            "relation_types": [{"type": k, "count": v} for k, v in sorted(type_counts.items())],
            "node_count": sum(label_counts.values()),
            "relation_count": sum(type_counts.values()),
        }

    def _filter_dict(self, label: Optional[str], filters: Sequence[Tuple[str, str, Any]]) -> Dict[str, Any]:
        conditions: List[Dict[str, Any]] = []
        if label is not None:
            conditions.append({"main_label": _identifier(label, "label")})
        if len(filters) > MAX_FILTERS:
            raise InvalidRequest(f"At most {MAX_FILTERS} filters are allowed")
        for entry in filters:
            try:
                prop, op, value = entry
            except (TypeError, ValueError):
                raise InvalidRequest("A filter is (property, operator, value)") from None
            prop = _identifier(prop, "property name")
            if op not in FILTER_OPERATORS:
                raise InvalidRequest(f"Unknown filter operator {op!r}; use one of {', '.join(FILTER_OPERATORS)}")
            value = "" if value is None else str(value)
            if len(value) > MAX_TEXT_LENGTH:
                raise InvalidRequest(f"Filter values are limited to {MAX_TEXT_LENGTH} characters")
            if op == "exists":
                condition: Any = {"$exists": True}
            elif op == "eq":
                condition = {"$in": _coerce_scalar(value)}
            elif op == "contains":
                # Escaped, case-insensitive "anywhere in the text". The leading
                # and trailing .* make it work both where $regex is a search
                # (NetworkX) and where it is a full match (Neo4j's =~).
                condition = {"$regex": "(?is).*" + re.escape(value) + ".*"}
            elif op == "ne":
                condition = {"$nin": _coerce_scalar(value)}
            else:
                condition = {_COMPARISON_OPS[op]: _comparable(value)}
            conditions.append({prop: condition})
        if not conditions:
            return {}
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def search(
        self,
        label: Optional[str] = None,
        filters: Sequence[Tuple[str, str, Any]] = (),
        text: Optional[str] = None,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> Dict[str, Any]:
        """Find nodes by main label, property filters and free text.

        Args:
            label: Main label to restrict to.
            filters: ``(property, operator, value)`` triples, all of which
                must hold. Operators: ``eq``, ``ne`` (numbers/booleans in
                *value* also match typed values), ``contains``
                (case-insensitive substring), ``gt``/``gte``/``lt``/``lte``,
                ``exists`` (value ignored).
            text: Case-insensitive text looked for in every string property.
            offset: Results to skip (paging).
            limit: Results to return; capped at ``max_page_size``.

        Returns:
            A dict with ``items`` (node summaries), ``total``,
            ``total_is_exact``, ``offset`` and ``limit``. At most ``max_scan`` nodes are read, so
            past that ``total`` is a lower bound and ``total_is_exact`` is
            False.

        Raises:
            InvalidRequest: On an invalid label, property, operator or range.
        """
        offset = _int_in_range(offset, "offset", 0)
        limit = min(_int_in_range(limit, "limit", 1), self.max_page_size)
        if offset >= self.max_scan:
            raise InvalidRequest(f"offset must be < {self.max_scan} (the scan limit)")
        if text is not None:
            if not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH:
                raise InvalidRequest(f"Search text is limited to {MAX_TEXT_LENGTH} characters")
            text = text.strip().casefold() or None
        filter_dict = self._filter_dict(label, list(filters))

        rows = self._find(filter_dict, self.max_scan + 1)
        if text:
            rows = [
                row for row in rows
                if any(text in s.casefold()
                       for key, value in (row.get("properties") or {}).items()
                       if key not in _INTERNAL_KEYS
                       for s in _text_values(value))
            ]
        exact = len(rows) <= self.max_scan
        rows = rows[: self.max_scan]
        rows.sort(key=lambda row: (str(type(row.get("node_id"))), row.get("node_id")))
        page = rows[offset: offset + limit]
        return {
            "items": [
                self._summary_of(row.get("node_id"), [], row.get("main_label"),
                                 row.get("properties"), SUMMARY_STRING_LENGTH)
                for row in page
            ],
            "total": len(rows),
            "total_is_exact": exact,
            "offset": offset,
            "limit": limit,
        }

    def node(self, node_id: int) -> Dict[str, Any]:
        """All the labels and properties of one node.

        Raises:
            InvalidRequest: If *node_id* isn't an integer.
            NodeNotFound: If there is no such node.
        """
        node_id = _node_id(node_id)
        rows = self._cypher(_NODE_DETAIL, {"id": node_id})
        if not rows:
            raise NodeNotFound(f"No node with id {node_id}")
        row = rows[0]
        labels = list(row.get("labels") or [])
        return self._summary_of(node_id, labels, labels[0] if labels else None,
                                row.get("props"), DETAIL_STRING_LENGTH)

    def neighbours(
        self,
        node_id: int,
        direction: str = "both",
        relation_type: Optional[str] = None,
        limit: int = DEFAULT_NEIGHBOURS,
    ) -> Dict[str, Any]:
        """Relations of one node and the nodes at their other end.

        Args:
            node_id: The node.
            direction: ``"out"``, ``"in"`` or ``"both"``.
            relation_type: Only relations of this type.
            limit: Most relations to return; capped at ``max_neighbours``.

        Returns:
            A dict with ``node`` (its summary), ``relations`` (each with
            ``type``, ``direction``, ``source``, ``target``, ``properties``
            and ``neighbour``, the other node's summary) and ``truncated``.

        Raises:
            InvalidRequest: On an invalid argument.
            NodeNotFound: If there is no such node.
        """
        node_id = _node_id(node_id)
        if direction not in _DIRECTIONS:
            raise InvalidRequest(f"direction must be one of {', '.join(_DIRECTIONS)}")
        limit = min(_int_in_range(limit, "limit", 1), self.max_neighbours)
        params: Dict[str, Any] = {"id": node_id, "limit": limit + 1}
        type_clause = ""
        if relation_type is not None:
            params["rtype"] = _identifier(relation_type, "relation type")
            type_clause = " AND type(r) = $rtype"

        node = self.node(node_id)
        directions = ("out", "in") if direction == "both" else (direction,)
        relations: List[Dict[str, Any]] = []
        truncated = False
        for current in directions:
            rows = self._cypher(_NEIGHBOURS[current].format(type_clause=type_clause), params)
            for row in rows:
                if len(relations) >= limit:
                    truncated = True
                    break
                other = row.get("mid")
                labels = list(row.get("labels") or [])
                relations.append({
                    "type": str(row.get("type")),
                    "direction": current,
                    "source": node_id if current == "out" else other,
                    "target": other if current == "out" else node_id,
                    "properties": {k: _jsonable(v, SUMMARY_STRING_LENGTH)
                                   for k, v in sorted((row.get("rprops") or {}).items())},
                    "neighbour": self._summary_of(other, labels, labels[0] if labels else None,
                                                  row.get("props"), SUMMARY_STRING_LENGTH),
                })
            if truncated:
                break
        return {"node": node, "relations": relations, "truncated": truncated}

    def relations_among(self, node_ids: Sequence[int]) -> Dict[str, Any]:
        """The relations whose two ends are both in *node_ids*, in one query.

        Lets a client draw the edges of a whole canvas without asking for the
        neighbours of every node.

        Args:
            node_ids: Node ids (at most :data:`MAX_RELATION_IDS`); unknown ids
                are ignored.

        Returns:
            A dict with ``relations`` (each with ``type``, ``source``,
            ``target`` and ``properties``) and ``truncated``, true when there
            were more than ``max_relations``.

        Raises:
            InvalidRequest: On a non-integer id or too many ids.
        """
        if len(node_ids) > MAX_RELATION_IDS:
            raise InvalidRequest(f"At most {MAX_RELATION_IDS} node ids are allowed")
        ids = sorted({_node_id(node_id) for node_id in node_ids})
        if not ids:
            return {"relations": [], "truncated": False}
        rows = self._cypher(_RELATIONS_AMONG, {"ids": ids, "limit": self.max_relations + 1})
        relations = [
            {
                "type": str(row.get("type")),
                "source": row.get("source"),
                "target": row.get("target"),
                "properties": {k: _jsonable(v, SUMMARY_STRING_LENGTH)
                               for k, v in sorted((row.get("rprops") or {}).items())},
            }
            for row in rows[: self.max_relations]
        ]
        return {"relations": relations, "truncated": len(rows) > self.max_relations}
