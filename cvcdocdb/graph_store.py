"""Abstract graph store interface for the DRM model.

Both ``Neo4jGraph`` and ``NetworkXGraph`` implement this ABC so that
code in ``entities.py`` and elsewhere can accept any graph store
without knowing the backing technology.

Usage::

    from cvcdocdb.graph_store import GraphStore
    from cvcdocdb.networkx_graph import NetworkXGraph

    def process(store: GraphStore) -> None:
        node = Node(pk={"id": 1}, main_label="Test")
        store.insertNode(node)
        store.close()

    process(NetworkXGraph())
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Union

from .base import Node, Relation


class GraphStore(ABC):
    """Abstract interface for graph-backed stores (Neo4j, NetworkX, etc.).

    Every concrete implementation must support the same core operations:
    insert, update, replace, delete nodes and relations, bulk import,
    and existence checks.

    Subclasses must implement all abstract methods.
    """

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    @abstractmethod
    def insertNode(
        self,
        node: Node,
        insert_parent: bool = True,
        update: bool = False,
        replace: bool = False,
        **kwargs: Any,
    ) -> Union[int, str]:
        """Insert a node into the graph store.

        Args:
            node: The node to insert.
            insert_parent: If the node is a WeakNode, insert its parent
                first. Defaults to True.
            update: If True, MERGE the node and update attributes without
                deleting it. Defaults to False.
            replace: If True and the node already exists, delete it
                (with detach) and create a fresh one. Defaults to False.
            **kwargs: Additional implementation-specific parameters.

        Returns:
            The internal node identifier assigned by the store.
        """

    @abstractmethod
    def insertRelation(
        self,
        rel: Relation,
        update: bool = False,
        replace: bool = False,
        **kwargs: Any,
    ) -> Union[int, Tuple[int, int, str]]:
        """Insert a directed relation (edge) between two nodes.

        Args:
            rel: The relation to insert.
            update: If True, MERGE the relation and update attributes.
            replace: If True and the relation already exists, delete it
                and create a fresh one.
            **kwargs: Additional implementation-specific parameters.

        Returns:
            The internal relation identifier.
        """

    @abstractmethod
    def deleteNode(
        self,
        node: Node,
        propagation: bool = False,
        detach: bool = False,
        on_delete: str = "cascade",
    ) -> bool:
        """Delete a node from the graph store.

        Args:
            node: The node to delete.
            propagation: If True, recursively delete child nodes linked
                via edges with ``_propagate=TRUE``.
            detach: If True, delete the node and all connected edges.
            on_delete: Deletion strategy — ``"cascade"``, ``"restrict"``,
                or ``"set_null"``. Defaults to ``"cascade"``.

        Returns:
            True if the node was deleted, False otherwise.
        """

    @abstractmethod
    def checkNode(self, node: Node, **kwargs: Any) -> Optional[int]:
        """Check if a node exists in the graph store.

        Args:
            node: The node to look up.
            **kwargs: Additional implementation-specific parameters.

        Returns:
            The internal node id if found, None otherwise.
        """

    @abstractmethod
    def create(
        self,
        migration: Tuple[List, List],
        update: bool = False,
        replace: bool = False,
    ) -> None:
        """Bulk import nodes and relations.

        Args:
            migration: A tuple ``(node_list, relation_list)``.
            update: Passed to ``insertNode`` / ``insertRelation``.
            replace: Passed to ``insertNode`` / ``insertRelation``.
        """

    @abstractmethod
    def close(self) -> None:
        """Release resources and clear the graph store."""

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def query(
        self,
        filter_dict: Optional[Union[Dict[str, Any], str]] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[Tuple[str, int]] = None,
        limit_val: Optional[int] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Query nodes using MongoDB-style dictionary filters (NetworkX)
        or execute raw Cypher (Neo4j).

        **Hybrid API** — the first argument accepts either:

        * **dict** — MongoDB-style filter matching against
          ``main_label`` and node attributes.  Supports operators
          ``$eq``, ``$ne``, ``$gt``, ``$gte``, ``$lt``, ``$lte``,
          ``$in``, ``$nin``, ``$exists``, ``$regex``.
        * **str** — a Cypher query string (``MATCH``, ``CREATE``,
          ``DELETE``, ``SET``, ``RETURN``, ``ORDER BY``, ``LIMIT``,
          aggregations, parameter substitution via ``$name``).

        Args:
            filter_dict: Filter dict for MongoDB-style queries, or Cypher
                string for Cypher-style queries.
            projection: Dict of fields to include (1) or exclude (0).
                Ignored for Cypher queries (use ``RETURN`` aliases).
            sort: Tuple of ``(field_name, direction)`` where direction is
                ``1`` (ascending) or ``-1`` (descending).
                Ignored for Cypher queries (use ``ORDER BY``).
            limit_val: Maximum number of results to return.
                Ignored for Cypher queries (use ``LIMIT``).
            params: Optional parameter dict for ``$name`` substitution
                in Cypher queries. Ignored for MongoDB-style queries.

        Returns:
            A list of dicts, one per matching node (MongoDB-style) or
            one per result record (Cypher-style).

        Raises:
            NotImplementedError: If the backend does not support queries.
        """
        raise NotImplementedError(
            f"Query is not supported by {self.__class__.__name__}."
        )

    def count(
        self,
        filter_dict: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Count nodes matching the filter dict.

        Args:
            filter_dict: Dict of field/value pairs to match.

        Returns:
            Integer count of matching nodes.
        """
        raise NotImplementedError(
            f"Count is not supported by {self.__class__.__name__}."
        )

    # ------------------------------------------------------------------
    # Optional vector index API (concrete default)
    # ------------------------------------------------------------------

    def enable_vector_index(
        self,
        property_name: str,
        dimensions: int,
        space: str = "cosine",
        **kwargs: Any,
    ) -> None:
        """Enable vector indexing for one property if the backend supports it.

        Backends without ANN/vector support should keep the default behavior
        and raise ``NotImplementedError``.
        """
        raise NotImplementedError(
            f"Vector indexing is not supported by {self.__class__.__name__}."
        )

    def query_vector_index(
        self,
        property_name: str,
        vector: Union[List[float], Tuple[float, ...]],
        top_k: int = 10,
    ) -> List[Tuple[int, float]]:
        """Query nearest nodes for a vector if the backend supports it."""
        raise NotImplementedError(
            f"Vector indexing is not supported by {self.__class__.__name__}."
        )

    # ------------------------------------------------------------------
    # Query helpers (concrete default — may be overridden)
    # ------------------------------------------------------------------

    def get_node(self, node_id: int) -> Optional[Node]:
        """Retrieve a node by its internal id.

        Default implementation returns None. Subclasses may override.

        Args:
            node_id: The internal node id.

        Returns:
            A ``Node`` instance, or None.
        """
        return None

    def get_node_ids(self) -> List[int]:
        """Return all internal node ids in the graph.

        Returns the backend-specific internal ids (Neo4j ``id(n)``,
        NetworkX node keys).  These are opaque identifiers and must
        not be compared with primary keys.

        Default implementation returns an empty list.
        """
        return []

    def get_node_pks(self) -> List[Dict[str, Any]]:
        """Return all primary keys of nodes in the graph.

        Each element is a dict with ``main_label`` and ``pk`` keys
        matching the ``Node`` interface.

        Default implementation returns an empty list.
        """
        return []

    def get_edges(self) -> List[Tuple[int, int, str]]:
        """Return all edges as ``(src_node_id, dst_node_id, rel_type)`` tuples.

        The node ids match what ``get_node_ids()`` returns (backend-
        specific internal ids).

        Default implementation returns an empty list.
        """
        return []

    def get_node_attrs(self, node_id: int) -> Optional[Dict[str, Any]]:
        """Return attributes stored for a node.

        Default implementation returns None.
        """
        return None

    def get_edge_attrs(self, u: int, v: int, key: str) -> Optional[Dict[str, Any]]:
        """Return attributes stored for an edge.

        Default implementation returns None.
        """
        return None

    # ------------------------------------------------------------------
    # Debug helpers (concrete default — may be overridden)
    # ------------------------------------------------------------------

    def debug(self) -> Dict[str, Any]:
        """Return a human-readable snapshot of the graph state.

        Default implementation returns an empty dict.
        """
        return {"nodes": [], "edges": [], "fk_index": {}}

    def print_debug(self) -> None:
        """Print a formatted snapshot of the graph state to stdout."""
        state = self.debug()
        print("\n=== GraphStore State ===")
        print(f"Nodes ({len(state.get('nodes', []))}):")
        for item in state.get("nodes", []):
            print(f"  {item}")
        print(f"Edges ({len(state.get('edges', []))}):")
        for item in state.get("edges", []):
            print(f"  {item}")
        print("========================\n")

    # ------------------------------------------------------------------
    # Schema introspection
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Transactional group creation
    # ------------------------------------------------------------------

    def create_group(
        self,
        strong_node: Node,
        weak_nodes: Optional[List[Node]] = None,
        weak_relations: Optional[List[Relation]] = None,
        **kwargs: Any,
    ) -> int:
        """Create a strong node together with its WeakNodes and WeakRelations
        atomically.

        All nodes and relations are inserted in a single isolated
        transaction.  If any step fails the entire group is rolled back
        so the graph is never left in a partially-created state.

        Args:
            strong_node: The root (non-weak) node of the group.
            weak_nodes: Optional list of WeakNode instances belonging to
                this group.
            weak_relations: Optional list of WeakRelation instances that
                connect the strong node (or other nodes) to the WeakNodes.

        Returns:
            The internal id of the ``strong_node``.

        Raises:
            RuntimeError: If any part of the group creation fails —
                the transaction is rolled back automatically.
        """
        raise NotImplementedError(
            f"create_group is not supported by {self.__class__.__name__}."
        )

    # ------------------------------------------------------------------
    # Propagation property initialization
    # ------------------------------------------------------------------

    def init_propagation(
        self,
        background: bool = False,
        progress_callback: Optional[callable] = None,
    ) -> bool:
        """Scan the backend graph and initialize propagation properties
        on nodes and edges that are missing them.

        This method inspects every node and edge, determines whether it
        participates in a WeakNode / WeakRelation hierarchy, and sets
        the corresponding ``_propagate``, ``is_weak``, ``parent_relation``,
        and ``_dependencies`` properties.

        **Lazy + background approach**: the first call runs synchronously
        and marks the graph as initialized.  Subsequent calls return
        ``False`` immediately.  For large graphs set ``background=True``
        to run the scan in a background thread (the method still returns
        ``True`` once initialization completes).

        Args:
            background: If True, run the scan in a background thread.
                The method returns immediately after starting the thread.
            progress_callback: Optional callable ``(processed, total)``
                called periodically during the scan.

        Returns:
            ``True`` if initialization was performed (or is running in
            the background), ``False`` if already initialized.
        """
        raise NotImplementedError(
            f"init_propagation is not supported by {self.__class__.__name__}."
        )

    @abstractmethod
    def schema_yaml(self, db_name: str) -> str:
        """Introspect the database and return a YAML schema description.

        The YAML contains labels, properties, relationship types,
        counts, and Python class names derived from the schema.

        Args:
            db_name: Human-readable database name (e.g. ``"got"``).

        Returns:
            A YAML string suitable for code generation.
        """

    # ------------------------------------------------------------------
    # Subdocument queries
    # ------------------------------------------------------------------

    def get_subdocuments(
        self,
        strong_node: Node,
    ) -> List[Dict[str, Any]]:
        """Return all subdocuments (WeakNodes) reachable from a strong node
        through ``_propagate`` edges.

        This method follows edges in their declared direction
        (``parent → child``) and returns every descendant WeakNode
        as a dict with ``label``, ``pk``, and ``properties`` keys.

        Args:
            strong_node: The root (non-weak) node whose subdocuments
                should be retrieved.

        Returns:
            A list of dicts, one per subdocument:

            .. code-block:: python

                {
                    "label": "Section",
                    "pk": {"section": "intro"},
                    "properties": {"title": "Introduction", ...},
                }

        Raises:
            NotImplementedError: If the backend does not support
                subdocument queries.
        """
        raise NotImplementedError(
            f"get_subdocuments is not supported by {self.__class__.__name__}."
        )
