__version__ = "0.0.0.dev0"  # real version numbers only live on `main` — see CLAUDE.md branch strategy

from .base import *
from .drm_entities import *
from .graph_store import GraphStore


def __getattr__(name):
    """Lazy import for optional backend modules.

    Neo4jGraph and NetworkXGraph require optional dependencies (neo4j and
    networkx respectively) that may not be installed in all environments.
    """
    if name == "Neo4jGraph":
        from .neo4j_graph import Neo4jGraph

        return Neo4jGraph
    if name == "NetworkXGraph":
        from .networkx_graph import NetworkXGraph

        return NetworkXGraph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
