"""Routing of the viewer's JSON API, shared by the HTTP server and the
notebook widget.

Paths and parameters are the server's (see :mod:`cvcdocdb.viewer.server`);
*query* is a :func:`urllib.parse.parse_qs` dict, ``{name: [values]}``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from .data import GraphViewerData, InvalidRequest

__all__ = ["UnknownEndpoint", "handle_api"]

_NODE_RE = re.compile(r"^/api/nodes/([^/]+)$")
_NEIGHBOURS_RE = re.compile(r"^/api/nodes/([^/]+)/neighbours$")


class UnknownEndpoint(LookupError):
    """No API endpoint matches the path."""


def _single(query: Dict[str, List[str]], name: str) -> Optional[str]:
    values = query.get(name)
    if not values:
        return None
    if len(values) > 1:
        raise InvalidRequest(f"Parameter {name!r} given more than once")
    return values[0]


def _int_param(query: Dict[str, List[str]], name: str, default: int) -> int:
    raw = _single(query, name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise InvalidRequest(f"Parameter {name!r} must be an integer") from None


def _parse_node_id(raw: str) -> int:
    try:
        return int(unquote(raw))
    except ValueError:
        raise InvalidRequest("Node id must be an integer") from None


def _parse_filters(values: List[str]) -> List[Tuple[str, str, str]]:
    filters = []
    for raw in values:
        parts = raw.split(":", 2)
        if len(parts) < 2:
            raise InvalidRequest("Filters look like property:operator:value")
        prop, op = parts[0], parts[1]
        filters.append((prop, op, parts[2] if len(parts) == 3 else ""))
    return filters


def handle_api(data: GraphViewerData, path: str, query: Dict[str, List[str]]) -> Any:
    """Answer one API request.

    Raises:
        UnknownEndpoint: If *path* is no API endpoint.
        InvalidRequest: On an invalid parameter.
        NodeNotFound: If the requested node doesn't exist.
    """
    if path == "/api/summary":
        return data.summary()
    if path == "/api/nodes":
        label = _single(query, "label")
        return data.search(
            label=label or None,
            filters=_parse_filters(query.get("f", [])),
            text=_single(query, "q"),
            offset=_int_param(query, "offset", 0),
            limit=_int_param(query, "limit", 50),
        )
    match = _NEIGHBOURS_RE.match(path)
    if match:
        node_id = _parse_node_id(match.group(1))
        return data.neighbours(
            node_id,
            direction=_single(query, "direction") or "both",
            relation_type=_single(query, "type") or None,
            limit=_int_param(query, "limit", 50),
        )
    match = _NODE_RE.match(path)
    if match:
        return data.node(_parse_node_id(match.group(1)))
    raise UnknownEndpoint(path)
