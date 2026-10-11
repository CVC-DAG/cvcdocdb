"""HTTP server of the graph viewer (standard library only).

Routes (``GET`` only — anything else gets ``405``):

* ``/`` — the viewer page; ``/static/<file>`` — its scripts and styles.
* ``/api/summary`` — :meth:`GraphViewerData.summary`.
* ``/api/nodes?label=&q=&f=prop:op:value&offset=&limit=`` —
  :meth:`GraphViewerData.search` (``f`` may be repeated; the value is
  everything after the second ``:``).
* ``/api/nodes/<id>`` — :meth:`GraphViewerData.node`.
* ``/api/nodes/<id>/neighbours?direction=&type=&limit=`` —
  :meth:`GraphViewerData.neighbours`.

When a *token* is configured, every ``/api`` request must carry it as
``?token=`` or ``Authorization: Bearer <token>`` (``401`` otherwise). The
page and its static files hold no data and are served without it; the page
forwards the token from its own URL to the API.

The server binds to ``127.0.0.1`` by default. While bound to a loopback
address it only answers requests whose ``Host`` is a loopback name, which
blocks DNS-rebinding attacks from web pages open in the same browser.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import socket
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, unquote, urlsplit

from ..graph_store import GraphStore
from .data import GraphViewerData, InvalidRequest, NodeNotFound

__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "ViewerServer", "create_server", "serve_viewer"]

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_URL_LENGTH = 8192

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_STATIC_FILES = {
    "viewer.js": "text/javascript; charset=utf-8",
    "viewer.css": "text/css; charset=utf-8",
    "vendor/cytoscape.min.js": "text/javascript; charset=utf-8",
    "vendor/cytoscape.LICENSE.txt": "text/plain; charset=utf-8",
}
_NODE_RE = re.compile(r"^/api/nodes/([^/]+)$")
_NEIGHBOURS_RE = re.compile(r"^/api/nodes/([^/]+)/neighbours$")
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    # Everything is served from this origin; cytoscape sets inline styles.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'none'"
    ),
}


def _static_bytes(name: str) -> bytes:
    return (resources.files(__package__) / "static").joinpath(*name.split("/")).read_bytes()


class _HttpError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


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


class _Handler(BaseHTTPRequestHandler):
    server: "ViewerServer"
    server_version = "cvcdocdb-viewer"
    sys_version = ""

    # -- plumbing ----------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        logger.debug("%s - %s", self.address_string(), format % args)

    def _send(self, status: HTTPStatus, body: bytes, content_type: str,
              extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in _SECURITY_HEADERS.items():
            self.send_header(key, value)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _reject_method(self) -> None:
        body = json.dumps({"error": "The viewer is read-only: only GET is allowed"}).encode("utf-8")
        self._send(HTTPStatus.METHOD_NOT_ALLOWED, body, "application/json; charset=utf-8",
                   {"Allow": "GET", "Cache-Control": "no-store"})

    do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_TRACE = _reject_method  # noqa: N815

    # -- checks ------------------------------------------------------------

    def _check_host(self) -> None:
        if not self.server.restrict_host:
            return
        host = (self.headers.get("Host") or "").strip()
        if host.startswith("["):
            name = host[1:].split("]", 1)[0]
        else:
            name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        if name.lower() not in self.server.allowed_hosts:
            raise _HttpError(HTTPStatus.FORBIDDEN, "Host not allowed")

    def _check_token(self, query: Dict[str, List[str]]) -> None:
        expected = self.server.token
        if not expected:
            return
        supplied = None
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        elif query.get("token"):
            supplied = query["token"][0]
        if supplied is None or not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            raise _HttpError(HTTPStatus.UNAUTHORIZED, "Missing or invalid token")

    # -- routing -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        try:
            if len(self.path) > MAX_URL_LENGTH:
                raise _HttpError(HTTPStatus.REQUEST_URI_TOO_LONG, "URL too long")
            self._check_host()
            parts = urlsplit(self.path)
            path = parts.path
            if path.startswith("/api/") or path == "/api":
                query = parse_qs(parts.query, keep_blank_values=True)
                self._check_token(query)
                self._send_json(HTTPStatus.OK, self._api(path, query))
            else:
                self._static(path)
        except _HttpError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except InvalidRequest as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except NodeNotFound as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except Exception:  # noqa: BLE001 - never leak internals to the client
            logger.exception("Graph viewer request failed: %s", self.path)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal error (see the server log)"})

    def _static(self, path: str) -> None:
        if path in ("/", "/index.html"):
            self._send(HTTPStatus.OK, _static_bytes("index.html"), "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            # Whitelist: no path built from the request ever reaches the disk.
            content_type = _STATIC_FILES.get(name)
            if content_type is not None:
                self._send(HTTPStatus.OK, _static_bytes(name), content_type)
                return
        raise _HttpError(HTTPStatus.NOT_FOUND, "Not found")

    def _api(self, path: str, query: Dict[str, List[str]]) -> Any:
        data = self.server.data
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
        raise _HttpError(HTTPStatus.NOT_FOUND, "Unknown API endpoint")


class ViewerServer(ThreadingHTTPServer):
    """The viewer's HTTP server; :attr:`url` is the address to open."""

    daemon_threads = True

    def __init__(self, data: GraphViewerData, host: str, port: int, token: Optional[str]) -> None:
        self.data = data
        self.token = token or None
        self.restrict_host = host in _LOOPBACK_HOSTS
        self.allowed_hosts = set(_LOOPBACK_HOSTS) | {host.lower()}
        if ":" in host:
            self.address_family = socket.AF_INET6
        super().__init__((host, port), _Handler)

    @property
    def url(self) -> str:
        """Base URL of the viewer page (with the token, if any)."""
        host, port = self.server_address[:2]
        shown = f"[{host}]" if ":" in str(host) else host
        url = f"http://{shown}:{port}/"
        return f"{url}?token={self.token}" if self.token else url


def create_server(
    store: Union[GraphStore, GraphViewerData],
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    token: Optional[str] = None,
) -> ViewerServer:
    """Build (but don't start) the viewer server for *store*.

    Args:
        store: The graph store to browse, or a configured
            :class:`GraphViewerData` (to change its limits).
        host: Address to bind; ``127.0.0.1`` by default. Binding anything
            else exposes the data to the network — set a *token*.
        port: TCP port; ``0`` picks a free one.
        token: If set, required by every ``/api`` request.

    Returns:
        A :class:`ViewerServer`; call ``serve_forever()`` to run it.
    """
    data = store if isinstance(store, GraphViewerData) else GraphViewerData(store)
    if host not in _LOOPBACK_HOSTS and not token:
        logger.warning("Graph viewer bound to %s without a token: anyone who can reach it can read the graph", host)
    return ViewerServer(data, host, port, token)


def serve_viewer(
    store: Union[GraphStore, GraphViewerData],
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    token: Optional[str] = None,
    open_browser: bool = False,
) -> None:
    """Serve the read-only viewer for *store* until interrupted (Ctrl+C).

    Args:
        store: The graph store to browse (see :func:`create_server`).
        host: Address to bind; ``127.0.0.1`` by default.
        port: TCP port (default 8765); ``0`` picks a free one.
        token: If set, required by every ``/api`` request.
        open_browser: Open the page in the default browser.

    The store is not closed on exit: it belongs to the caller.
    """
    server = create_server(store, host=host, port=port, token=token)
    print(f"cvcdocdb graph viewer: {server.url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
