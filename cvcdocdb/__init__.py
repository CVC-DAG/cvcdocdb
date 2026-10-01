from importlib.metadata import PackageNotFoundError, version as _version

try:
    # Single source of truth: VERSION in setup.py, via the installed metadata.
    __version__ = _version("cvcdocdb")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0.dev0"

import warnings as _warnings

from .base import *
from .graph_store import GraphLockTimeout, GraphStore

# cvcdocdb.drm_entities és un mòdul opcional: el paquet ja no l'importa.
# Aquests noms continuen accessibles des de `cvcdocdb` (i amb `import *`),
# però amb un DeprecationWarning; a la propera versió major se suprimeixen.
_DRM_ENTITY_NAMES = (
    "assert_on_properties",
    "Individu", "IndividuPadro", "IndividuFoto",
    "Lloc", "LlocPadro", "LlocFoto",
    "DocumentCultural", "Fons", "ActaTemporal",
    "EntitatAmbNom", "IndividuAgregat", "Esdeventiment",
    "Layout", "RegioFisica", "OCRTranscript",
    "Padro", "Fotografia", "BOE",
    "NODES", "EDGES",
)


def __getattr__(name):
    """Lazy import for optional backend modules.

    Neo4jGraph/MemgraphGraph and NetworkXGraph require optional dependencies (neo4j and
    networkx respectively) that may not be installed in all environments;
    Text2Cypher on a Neo4jGraph additionally needs ``neo4j-graphrag``
    (``cvcdocdb[graphrag]``).
    """
    if name == "Neo4jGraph":
        from .neo4j_graph import Neo4jGraph

        return Neo4jGraph
    if name == "MemgraphGraph":
        from .memgraph_graph import MemgraphGraph

        return MemgraphGraph
    if name == "NetworkXGraph":
        from .networkx_graph import NetworkXGraph

        return NetworkXGraph
    if name in _DRM_ENTITY_NAMES:
        _warnings.warn(
            f"Importing {name!r} from 'cvcdocdb' is deprecated: cvcdocdb.drm_entities is "
            f"an optional module. Use 'from cvcdocdb.drm_entities import {name}' instead; "
            "the top-level name will be removed in the next major version.",
            DeprecationWarning,
            stacklevel=2,
        )
        from . import drm_entities

        return getattr(drm_entities, name)
    if name in ("Text2Cypher", "Text2CypherResult", "Text2CypherError", "CallableLLM"):
        from . import text2cypher

        return getattr(text2cypher, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# `from cvcdocdb import *` exporta el mateix que abans, entitats DRM incloses
# (que passen per __getattr__ i, per tant, avisen).
__all__ = [_name for _name in dict(globals()) if not _name.startswith("_")] + list(_DRM_ENTITY_NAMES)
