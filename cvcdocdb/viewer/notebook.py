"""The graph viewer as a Jupyter widget (needs ``pip install cvcdocdb[notebook]``).

::

    from cvcdocdb.viewer import show

    show(graph)            # last expression of a cell

No HTTP server is involved, so it can run wherever anywidget does (JupyterLab,
Notebook 7, VS Code, Colab; tested in JupyterLab). The viewer page runs in a sandboxed ``iframe``
that has no network access; it asks the widget for data with
``postMessage``, the widget forwards the request to the kernel, and the
kernel answers it with :func:`cvcdocdb.viewer.api.handle_api` — the same
read-only API the HTTP server exposes. The graph is live: it needs a running
kernel, and a saved notebook reopened without one shows no data.
"""

from __future__ import annotations

import json
import logging
from importlib import resources
from typing import Any, Dict, List, Tuple, Union

try:
    import anywidget
    import traitlets
except ImportError as exc:  # pragma: no cover - depends on the environment
    raise ImportError(
        "The notebook viewer needs anywidget: pip install 'cvcdocdb[notebook]'"
    ) from exc

from ..graph_store import GraphStore
from .api import UnknownEndpoint, handle_api
from .data import GraphViewerData, InvalidRequest, NodeNotFound

__all__ = ["GraphViewerWidget", "build_page"]

logger = logging.getLogger(__name__)

MESSAGE_TYPE = "cvcdocdb-api"
REFRESH_TYPE = "cvcdocdb-refresh"
DEFAULT_HEIGHT = 650

# The frame only runs the inlined scripts and styles: no network at all.
_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"
)


def _static_text(name: str) -> str:
    return (resources.files(__package__) / "static").joinpath(*name.split("/")).read_text("utf-8")


def _script(code: str) -> str:
    # "</script" would end the element early; it can only occur in strings.
    return "<script>" + code.replace("</script", "<\\/script") + "</script>"


def _replace_once(page: str, old: str, new: str) -> str:
    if page.count(old) != 1:
        raise RuntimeError(f"Unexpected viewer page template: {old!r} not found exactly once")
    return page.replace(old, new)


def build_page() -> str:
    """The viewer page with its styles and scripts inlined, set to ask its
    parent window for data instead of the HTTP API."""
    page = _static_text("index.html")
    page = _replace_once(
        page,
        '<meta charset="utf-8">',
        f'<meta charset="utf-8">\n  <meta http-equiv="Content-Security-Policy" content="{_CSP}">',
    )
    page = _replace_once(
        page,
        '<link rel="stylesheet" href="static/viewer.css">',
        "<style>" + _static_text("viewer.css") + "</style>",
    )
    page = _replace_once(
        page,
        '<script src="static/vendor/cytoscape.min.js"></script>',
        _script(_static_text("vendor/cytoscape.min.js")),
    )
    return _replace_once(
        page,
        '<script src="static/viewer.js"></script>',
        _script("window.CVCDOCDB_BRIDGE = true;") + _script(_static_text("viewer.js")),
    )


def _query(params: Any) -> Dict[str, List[str]]:
    """``[[name, value], ...]`` from the page → a ``parse_qs``-style dict."""
    if not isinstance(params, list):
        raise InvalidRequest("Malformed request")
    query: Dict[str, List[str]] = {}
    for pair in params:
        if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(p, str) for p in pair)):
            raise InvalidRequest("Malformed request")
        query.setdefault(pair[0], []).append(pair[1])
    return query


class GraphViewerWidget(anywidget.AnyWidget):
    """Read-only graph viewer for *store*, displayed in a notebook cell.

    Args:
        store: The graph store to browse, or a configured
            :class:`~cvcdocdb.viewer.data.GraphViewerData` (to change its limits).
        height: Height of the viewer in pixels.
        load_all: Show the whole graph on the canvas (up to 300 nodes) and
            keep it whole on :meth:`refresh` — for small graphs, such as a
            tutorial's.

    The viewer reads the store when the user asks for something. After
    changing the graph from Python, call :meth:`refresh` to update it.
    """

    _esm = _static_text("widget.js")
    height = traitlets.Int(DEFAULT_HEIGHT).tag(sync=True)
    load_all = traitlets.Bool(False).tag(sync=True)
    _page = traitlets.Unicode("").tag(sync=True)

    def __init__(self, store: Union[GraphStore, GraphViewerData], height: int = DEFAULT_HEIGHT,
                 load_all: bool = False, **kwargs: Any) -> None:
        super().__init__(height=height, load_all=load_all, _page=build_page(), **kwargs)
        self.data = store if isinstance(store, GraphViewerData) else GraphViewerData(store)
        self.on_msg(self._handle_message)

    def refresh(self) -> None:
        """Read the store again and update every view of the widget: the
        sidebar, the table and the canvas (deleted nodes go away; expanded
        nodes, or the whole graph with *load_all*, are fetched again)."""
        self.send({"type": REFRESH_TYPE})

    def _answer(self, path: Any, params: Any) -> Tuple[int, Any]:
        try:
            if not isinstance(path, str):
                raise InvalidRequest("Malformed request")
            query = _query(params)
            return 200, handle_api(self.data, "/" + path.lstrip("/"), query)
        except InvalidRequest as exc:
            return 400, {"error": str(exc)}
        except (NodeNotFound, UnknownEndpoint) as exc:
            return 404, {"error": str(exc) if isinstance(exc, NodeNotFound) else "Unknown API endpoint"}
        except Exception:  # noqa: BLE001 - never leak internals to the page
            logger.exception("Graph viewer request failed: %s", path)
            return 500, {"error": "Internal error (see the kernel log)"}

    def _handle_message(self, widget: Any, content: Any, buffers: Any) -> None:
        if not isinstance(content, dict) or content.get("type") != MESSAGE_TYPE:
            return
        status, body = self._answer(content.get("path"), content.get("params"))
        # Same serialisation as the server: anything odd becomes a string.
        body = json.loads(json.dumps(body, ensure_ascii=False, default=str))
        self.send({"type": MESSAGE_TYPE, "id": content.get("id"), "ok": status == 200,
                   "status": status, "body": body})
