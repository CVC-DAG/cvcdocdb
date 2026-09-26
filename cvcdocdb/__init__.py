__version__ = "1.0.0"

from .base import *
from .drm_entities import *
from .graph_store import GraphStore


def __getattr__(name):
    """Lazy import for optional backend modules.

    Neo4jGraph and NetworkXGraph require optional dependencies (neo4j and
    networkx respectively) that may not be installed in all environments;
    Text2Cypher on a Neo4jGraph additionally needs ``neo4j-graphrag``
    (``cvcdocdb[graphrag]``).
    """
    if name == "Neo4jGraph":
        from .neo4j_graph import Neo4jGraph

        return Neo4jGraph
    if name == "NetworkXGraph":
        from .networkx_graph import NetworkXGraph

        return NetworkXGraph
    if name in ("Text2Cypher", "Text2CypherResult", "Text2CypherError", "CallableLLM"):
        from . import text2cypher

        return getattr(text2cypher, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
