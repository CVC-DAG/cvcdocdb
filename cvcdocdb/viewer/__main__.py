"""Command line of the graph viewer.

Local NetworkX pickle::

    python -m cvcdocdb.viewer path/to/graph.pkl

Remote backend (the password is read from ``CVCDOCDB_PASSWORD`` or asked
for; Jena/Memgraph may need none)::

    python -m cvcdocdb.viewer --backend neo4j --url bolt://localhost:7687 --user neo4j [--database db]
    python -m cvcdocdb.viewer --backend memgraph --url bolt://localhost:7688
    python -m cvcdocdb.viewer --backend jena --url http://localhost:3030/ds

Common options: ``--host`` (default 127.0.0.1), ``--port`` (default 8765,
0 = any free port), ``--token`` (or ``CVCDOCDB_VIEWER_TOKEN``) to require a
token on the API, ``--no-browser`` not to open the page. Page look:
``--layout light`` (graph first), ``--load-all``, ``--accent '#RRGGBB'`` and
``--css FILE`` (a stylesheet added after the viewer's own); ``--language ca``
(or ``es``) for the interface texts.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from typing import Any, List, Optional

from .page import LANGUAGES, LAYOUTS
from .server import DEFAULT_HOST, DEFAULT_PORT, serve_viewer

PASSWORD_ENV = "CVCDOCDB_PASSWORD"
TOKEN_ENV = "CVCDOCDB_VIEWER_TOKEN"
REMOTE_BACKENDS = ("neo4j", "memgraph", "jena")


def _backend_class(name: str) -> Any:
    """The GraphStore class of a remote backend (imported lazily)."""
    if name == "neo4j":
        from ..neo4j_graph import Neo4jGraph

        return Neo4jGraph
    if name == "memgraph":
        from ..memgraph_graph import MemgraphGraph

        return MemgraphGraph
    from ..jena_graph import JenaGraph

    return JenaGraph


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cvcdocdb.viewer",
        description="Read-only web viewer for the data in a cvcdocdb graph store.",
    )
    parser.add_argument("pickle", nargs="?", help="NetworkXGraph pickle file to open (local backend)")
    parser.add_argument("--backend", choices=REMOTE_BACKENDS, help="Remote backend instead of a pickle")
    parser.add_argument("--url", help="Remote backend URL (bolt://... or the Fuseki dataset URL)")
    parser.add_argument("--user", help="Remote backend user")
    parser.add_argument("--database", help="Neo4j/Memgraph database name")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Address to bind (default {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default {DEFAULT_PORT}; 0 = any)")
    parser.add_argument("--token", help=f"Token required by the API (default: ${TOKEN_ENV}, if set)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open the page in a browser")
    parser.add_argument("--layout", choices=LAYOUTS, default="classic", help="Page layout (light: graph first)")
    parser.add_argument("--load-all", action="store_true", help="Draw the whole graph on start (up to 300 nodes)")
    parser.add_argument("--accent", help="Accent colour of the page, e.g. '#00843D'")
    parser.add_argument("--css", metavar="FILE", help="Stylesheet added after the viewer's own")
    parser.add_argument("--language", choices=LANGUAGES, default="en",
                        help="Interface language (en, ca or es; default en)")
    return parser


def _open_store(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Any:
    if args.backend and args.pickle:
        parser.error("give either a pickle file or --backend, not both")
    if args.backend:
        if not args.url:
            parser.error("--backend needs --url")
        password = os.environ.get(PASSWORD_ENV)
        if password is None and args.user:
            password = getpass.getpass(f"Password for {args.user}: ")
        cls = _backend_class(args.backend)
        if args.backend == "jena":
            return cls(args.url, args.user, password)
        kwargs = {"database": args.database} if args.database else {}
        return cls(args.url, args.user or "", password or "", **kwargs)
    if not args.pickle:
        parser.error("give a NetworkXGraph pickle file or --backend")
    if not os.path.isfile(args.pickle):
        # NetworkXGraph would silently start an empty graph on a missing path.
        parser.error(f"no such file: {args.pickle}")
    from ..networkx_graph import NetworkXGraph

    return NetworkXGraph(persistence_path=os.path.abspath(args.pickle))


def main(argv: Optional[List[str]] = None) -> int:
    """Run the viewer; returns the process exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    extra_css = None
    if args.css:
        try:
            with open(args.css, encoding="utf-8") as css_file:
                extra_css = css_file.read()
        except OSError as exc:
            parser.error(f"can't read --css file: {exc}")
    store = _open_store(args, parser)
    token = args.token if args.token is not None else os.environ.get(TOKEN_ENV) or None
    try:
        serve_viewer(store, host=args.host, port=args.port, token=token, open_browser=not args.no_browser,
                     layout=args.layout, load_all=args.load_all, accent=args.accent, extra_css=extra_css,
                     language=args.language)
    finally:
        store.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
