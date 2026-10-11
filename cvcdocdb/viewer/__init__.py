"""Interactive, read-only viewer for the data in any cvcdocdb graph store.

Serves a single web page (vendored cytoscape.js, no network requests) plus a
small JSON API, using only the Python standard library::

    from cvcdocdb import NetworkXGraph
    from cvcdocdb.viewer import serve_viewer

    serve_viewer(NetworkXGraph(persistence_path="graph.pkl"))  # http://127.0.0.1:8765/

or from the command line::

    python -m cvcdocdb.viewer graph.pkl
    python -m cvcdocdb.viewer --backend neo4j --url bolt://localhost:7687 --user neo4j

The viewer never writes: it only calls the store's public read API
(:meth:`~cvcdocdb.graph_store.GraphStore.query` with dict filters and fixed,
parametrised read-only Cypher). See :mod:`cvcdocdb.viewer.data` and
:mod:`cvcdocdb.viewer.server`.
"""

from .data import FILTER_OPERATORS, GraphViewerData, InvalidRequest, NodeNotFound
from .server import DEFAULT_HOST, DEFAULT_PORT, ViewerServer, create_server, serve_viewer

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "FILTER_OPERATORS",
    "GraphViewerData",
    "InvalidRequest",
    "NodeNotFound",
    "ViewerServer",
    "create_server",
    "serve_viewer",
]
