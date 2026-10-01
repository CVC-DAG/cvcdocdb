"""MemgraphGraph — Memgraph backend for the DRM graph model.

`Memgraph <https://memgraph.com/>`_ speaks the Bolt protocol and openCypher,
so it is driven with the same ``neo4j`` Python driver, and
:class:`MemgraphGraph` reuses :class:`~cvcdocdb.neo4j_graph.Neo4jGraph`'s
logic unchanged. The change-propagation policy is therefore **identical** to
the Neo4j backend:

- **Insert**: a WeakNode inserts its parent first (``insert_parent``) and the
  parent→child relation carries ``_propagate=TRUE``. A WeakNode without its
  parent is refused (nothing is written), child keys must reference the
  parent's keys, dependencies become ``Valor`` nodes, and duplicate keys are
  refused.
- **Update**: ``update=True`` MERGEs the node and merges its attributes.
  ``replace=True`` deletes the existing node with propagation (its WeakNode
  descendants go too) and creates a fresh one.
- **Delete**: RESTRICT (default) refuses nodes with WeakNode children or
  edges. ``propagation=True`` recursively deletes WeakNode children,
  ``detach=True`` (CASCADE) removes the edges and keeps the neighbours, and
  ``on_delete="set_null"`` is also supported.
- **Relations**: FK validation of both endpoints, plus ``update``/``replace``.

``test/test_memgraph_graph.py`` runs the same propagation scenarios on both
backends and checks they leave the same graph and raise the same errors.

Only the operations whose Cypher differs are overridden here: listing and
creating pk indexes (``SHOW INDEX INFO`` / ``CREATE INDEX ON :L(p)``) and
listing labels and relationship types (Memgraph has no ``db.labels()``).
The Neo4j Enterprise operations of :mod:`cvcdocdb.neo4j_enterprise` are not
available.

Example:
    >>> graph = MemgraphGraph("bolt://localhost:7687", "", "")
    >>> graph.insertNode(Node(pk={"doc": "DOC-001"}, main_label="Document"))
    >>> graph.close()
"""

from __future__ import annotations

from typing import Any, List, Optional, Set, Tuple

from .neo4j_enterprise import COMMUNITY_EDITION, EnterpriseFeatureError
from .neo4j_graph import PK_INDEX_PREFIX, Neo4jGraph, _validate_cypher_identifier

#: Value of the ``index type`` column of ``SHOW INDEX INFO`` for indexes on
#: node properties (single or composite).
_LABEL_PROPERTY_INDEX = "label+property"


def _memgraph_pk_index_statement(main_label: str, props: Tuple[str, ...]) -> Tuple[str, str]:
    """``(index name, CREATE INDEX statement)`` for one pk shape.

    Memgraph indexes have no name; the returned name follows the Neo4j
    backend's convention (``cvcdocdb_pk_<Label>_<props>``) so callers such as
    :func:`cvcdocdb.migration.migrate` report the same thing on both.
    """
    _validate_cypher_identifier(main_label, "main_label")
    for prop in props:
        _validate_cypher_identifier(prop, "pk property")
    name = PK_INDEX_PREFIX + "_".join((main_label, *props))
    columns = ", ".join(f"`{prop}`" for prop in props)
    return name, f"CREATE INDEX ON :`{main_label}`({columns})"


class MemgraphGraph(Neo4jGraph):
    """Memgraph-backed graph store, with the same API and semantics as
    :class:`~cvcdocdb.neo4j_graph.Neo4jGraph`.

    Args:
        url: Bolt URL of the Memgraph server (e.g. ``bolt://localhost:7687``).
        user: Username (``""`` if authentication is disabled, Memgraph's
            default).
        password: Password (``""`` if authentication is disabled).
        database: Target database. Memgraph Community has a single database
            (``memgraph``); multi-tenancy needs a Memgraph Enterprise license.
        auto_pk_indexes: See :class:`~cvcdocdb.neo4j_graph.Neo4jGraph`.
        **driver_config: Forwarded to ``neo4j.GraphDatabase.driver()``.

    Raises:
        TypeError: If ``edition`` is passed — Neo4j editions don't apply.
    """

    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        database: Optional[str] = None,
        auto_pk_indexes: bool = False,
        **driver_config: Any,
    ) -> None:
        if "edition" in driver_config:
            raise TypeError(
                "MemgraphGraph() got an unexpected keyword argument 'edition': "
                "Neo4j editions don't apply to Memgraph"
            )
        super().__init__(
            url,
            user,
            password,
            database=database,
            auto_pk_indexes=auto_pk_indexes,
            edition=COMMUNITY_EDITION,
            **driver_config,
        )

    # ------------------------------------------------------------------
    # Backend hooks (Cypher that differs from Neo4j)
    # ------------------------------------------------------------------

    def _existing_node_index_keys(self) -> Set[Tuple[str, frozenset]]:
        keys: Set[Tuple[str, frozenset]] = set()
        for row in self._session.run("SHOW INDEX INFO"):
            if row["index type"] != _LABEL_PROPERTY_INDEX or not row["label"]:
                continue
            props = row["property"]
            # Memgraph 2.x retorna una cadena per als índexs d'una propietat.
            props = [props] if isinstance(props, str) else list(props)
            keys.add((row["label"], frozenset(props)))
        return keys

    @staticmethod
    def _pk_index_statement(main_label: str, props: Tuple[str, ...]) -> Tuple[str, str]:
        return _memgraph_pk_index_statement(main_label, props)

    def _list_labels(self) -> List[str]:
        rows = self._session.run("MATCH (n) UNWIND labels(n) AS label RETURN DISTINCT label")
        return sorted(row["label"] for row in rows)

    def _list_relationship_types(self) -> List[str]:
        rows = self._session.run("MATCH ()-[r]->() RETURN DISTINCT type(r) AS rel_type")
        return sorted(row["rel_type"] for row in rows)

    # ------------------------------------------------------------------
    # Neo4j Enterprise features: not available on Memgraph
    # ------------------------------------------------------------------

    def server_edition(self) -> str:
        """``"enterprise"`` if the Memgraph server has a valid Enterprise
        license (``SHOW LICENSE INFO``), ``"community"`` otherwise."""
        if self._server_edition is None:
            info = {row["license info"]: row["value"] for row in self._session.run("SHOW LICENSE INFO")}
            self._server_edition = "enterprise" if info.get("is_valid") is True else "community"
        return self._server_edition

    def drop_constraint(self, name: str) -> None:
        """Not supported: Memgraph constraints have no name."""
        raise NotImplementedError(
            "MemgraphGraph.drop_constraint(): Memgraph constraints are unnamed; "
            "use 'DROP CONSTRAINT ON (n:Label) ASSERT ...' through query()."
        )

    def _require_enterprise(self, operation: str) -> None:
        raise EnterpriseFeatureError(
            f"{operation}() is a Neo4j Enterprise feature and is not available "
            "on Memgraph (MemgraphGraph)."
        )
