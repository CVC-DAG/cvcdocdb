"""NetworkX-backed graph store — in-memory, no real Neo4j required."""

from __future__ import annotations

import hashlib
import os
import pickle
import tempfile
import threading
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple, Union

import numpy as np
import networkx as nx

try:
    import hnswlib
except ImportError:  # pragma: no cover - guarded at runtime in vector APIs
    hnswlib = None

try:
    from filelock import FileLock
except ImportError:  # pragma: no cover - guarded at runtime in __init__
    FileLock = None

from .base import Node, Relation, WeakRelation
from .graph_store import GraphStore


class NetworkXGraph(GraphStore):
    """In-memory graph store using NetworkX.

    Provides the same public interface as ``Neo4jGraph`` but stores
    nodes and edges in a NetworkX MultiDiGraph so tests can run
    without a real database.
    """

    def __init__(self, persistence_path: Optional[str] = None) -> None:
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self._node_attrs: Dict[int, Dict[str, Any]] = {}
        self._edge_attrs: Dict[Tuple[int, int, str], Dict[str, Any]] = {}
        self._node_counter: int = 0
        self._version: float = 0.0
        # FK index: node_id → set of (other_id, rel_type, direction)
        # direction: "out" means edge starts at this node, "in" means edge ends here
        self._fk_index: Dict[int, Set[Tuple[int, str, str]]] = {}
        self._pk_index: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], int] = {}
        self._property_index: Dict[Tuple[str, Any], Set[int]] = {}
        self._vector_indexes: Dict[str, Any] = {}
        self._vector_index_meta: Dict[str, Dict[str, Any]] = {}
        self._persistence_path = persistence_path or self._default_persistence_path()
        if FileLock is None:
            raise ImportError(
                "filelock is required for NetworkXGraph's cross-process write "
                "locking. Install it with: pip install filelock"
            )
        # Cross-process lock guarding the load-mutate-save cycle of every
        # mutating call (see _guarded_write). A single lock file next to
        # the persistence file, so two NetworkXGraph instances (in the
        # same process or different ones) pointing at the same
        # persistence_path never race on it.
        self._file_lock = FileLock(f"{self._persistence_path}.lock")
        self._load_state()
        # Internal tracking: node_id -> pk dict (for get_node_pks)
        self._node_pks: Dict[int, Dict[str, Any]] = {}
        # Propagation initialization tracking
        self._propagation_initialized: bool = False
        self._propagation_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API: CRUD operations
    # ------------------------------------------------------------------

    def insertNode(
        self,
        node: Node,
        insert_parent: bool = True,
        update: bool = False,
        replace: bool = False,
        **kwargs: Any,
    ) -> int:
        """Insert a node into the graph.

        Returns the internal node id assigned by the graph.
        """
        with self._guarded_write():
            # Handle parent insertion for weak nodes
            if node["is_weak"] and insert_parent and node["parent"] is not None:
                parent = node["parent"]
                parent_id: int = self._ensure_node_inserted(parent, update=True, replace=False)
                parent.neo4j_id = parent_id

            node_id = self._ensure_node_inserted(node, update=update, replace=replace)
            node.neo4j_id = node_id

            # Track node_id -> pk mapping for get_node_pks()
            if node._primary_key is not None:
                self._node_pks[node_id] = dict(node._primary_key)

            # If weak, create the parent relation with _propagate=True
            if node["is_weak"] and node["parent"] is not None:
                parent_id = node["parent"].neo4j_id
                self._graph.add_edge(parent_id, node_id, key=node["parent_relation"], rel_type=node["parent_relation"])
                self._edge_attrs[(parent_id, node_id, node["parent_relation"])] = {"_propagate": True}
                # Register parent-child edge in FK index (like Neo4jGraph)
                self._add_to_fk_index(parent_id, node_id, node["parent_relation"])

            # Handle dependencies: create Valor nodes and HAS_* edges
            deps = getattr(node, "_dependencies", None)
            if deps:
                for k in deps.keys():
                    v = deps[k]
                    id_v = self._ensure_node_inserted(v, update=True, replace=False)
                    v.neo4j_id = id_v
                    self.insertRelation(Relation(node, v, k.upper()), update=True)

        return node_id

    def insertRelation(
        self,
        rel: Relation,
        update: bool = False,
        replace: bool = False,
        **kwargs: Any,
    ) -> int:
        """Insert a relation (directed edge) between two nodes.

        Mirrors Neo4jGraph.insertRelation semantics:

        - ``update=True``: MERGE + SET — adds/updates attributes without
          deleting the relation.
        - ``update=False`` + ``replace=True``: deletes the existing
          relation and creates a fresh one.
        - ``update=False`` + ``replace=False``: creates a new relation.
          If one with the same src/dst/type already exists, raises
          ``RuntimeError`` (duplicate key).

        Returns the internal edge identifier (u, v, key).
        """
        with self._guarded_write():
            # Resolve src/dst ids: prefer neo4j_id, fall back to pk lookup
            src_id = self._resolve_node_id(rel["src"])
            dst_id = self._resolve_node_id(rel["dst"])

            if src_id is None or dst_id is None:
                missing = []
                if src_id is None:
                    src_pk = rel["src"].get("pk", "?")
                    src_label = rel["src"].get("main_label", "?")
                    missing.append(f"src(pk={src_pk}, label={src_label})")
                if dst_id is None:
                    dst_pk = rel["dst"].get("pk", "?")
                    dst_label = rel["dst"].get("main_label", "?")
                    missing.append(f"dst(pk={dst_pk}, label={dst_label})")
                raise RuntimeError(
                    f"FK violation: node(s) not found: {', '.join(missing)}. "
                    f"Insert them before creating the relation."
                )

            edge_key = rel["type"]
            exists = self._graph.has_edge(src_id, dst_id, key=edge_key)

            if exists:
                if replace:
                    # Delete existing relation and create fresh
                    self._graph.remove_edge(src_id, dst_id, key=edge_key)
                    if (src_id, dst_id, edge_key) in self._edge_attrs:
                        del self._edge_attrs[(src_id, dst_id, edge_key)]
                    self._remove_from_fk_index(src_id, dst_id, edge_key)
                elif update:
                    # MERGE + SET: update attributes of existing relation
                    attrs = rel["attributes"] if rel["attributes"] is not None else {}
                    self._edge_attrs[(src_id, dst_id, edge_key)].update(attrs)
                    return (src_id, dst_id, edge_key)
                else:
                    # Duplicate key — same as Neo4j ConstraintError
                    raise RuntimeError(
                        f"Duplicate relation: ({src_id})-[{edge_key}]->({dst_id}) "
                        f"already exists. Use replace=True to overwrite or "
                        f"update=True to merge attributes."
                    )

            # Create new relation
            self._graph.add_edge(src_id, dst_id, key=edge_key, rel_type=rel["type"])
            attrs = rel["attributes"] if rel["attributes"] is not None else {}
            self._edge_attrs[(src_id, dst_id, edge_key)] = attrs
            self._add_to_fk_index(src_id, dst_id, edge_key)

            return (src_id, dst_id, edge_key)

    def deleteNode(
        self,
        node: Node,
        propagation: bool = False,
        detach: bool = False,
        on_delete: str = "cascade",
    ) -> bool:
        """Delete a node from the graph.

        ``on_delete`` controls FK behavior:

        - ``"cascade"`` (default): ON DELETE CASCADE — delete connected edges
        - ``"restrict"``: ON DELETE RESTRICT — refuse if edges exist
        - ``"set_null"``: ON DELETE SET NULL — delete node, keep neighbors
        """
        with self._guarded_write():
            node_id = node.neo4j_id
            if node_id is None:
                # Fall back to PK lookup
                node_id = self.checkNode(node)
            if node_id is None:
                return False

            if propagation:
                # Delete all connected nodes that have _propagate=True
                # Neo4j: MATCH (n)-[r]->(b) WHERE r._propagate=TRUE RETURN b
                # Also check if the parent node itself has propagate enabled
                parent_propagate = getattr(node, "_propagate", False)
                # Iterate over a copy since deleteNode mutates the graph
                neighbors = list(self._graph.successors(node_id))
                for neighbor in neighbors:
                    edges_data = list(self._graph[node_id][neighbor].items())
                    for key, data in edges_data:
                        edge_has_propagate = self._edge_attrs.get((node_id, neighbor, key), {}).get("_propagate")
                        if edge_has_propagate or parent_propagate:
                            child_node = Node(neo4j_id=neighbor)
                            self.deleteNode(child_node, propagation=propagation, detach=True)
                            # Stop iterating if node was deleted
                            break

            if on_delete == "set_null":
                # ON DELETE SET NULL: delete node, remove edges, keep neighbors
                self._set_null_delete(node_id)
                if self._graph.has_node(node_id):
                    self._graph.remove_node(node_id)
                return True

            if detach:
                # ON DELETE CASCADE: recursively delete connected edges and orphans
                self._cascade_delete(node_id)
                # Remove node from graph if it still exists
                if self._graph.has_node(node_id):
                    self._graph.remove_node(node_id)
            else:
                # ON DELETE RESTRICT: refuse to delete if edges exist
                if self._graph.degree(node_id) > 0:
                    raise RuntimeError(
                        f"ON DELETE RESTRICT: node {node_id} has edges and "
                        f"detach=False. Use detach=True or remove edges first."
                    )
                self._clean_fk_index(node_id)
                old_attrs = self._node_attrs.pop(node_id, None)
                if old_attrs is not None:
                    self._deindex_node(node_id, old_attrs)
                self._graph.remove_node(node_id)

            return True

    def checkNode(self, node: Node, **kwargs: Any) -> Optional[int]:
        """Check if a node exists in the graph.

        Looks up the node by its ``neo4j_id`` if set, otherwise searches
        by ``main_label`` and primary key.

        Args:
            node: The node to look up.

        Returns:
            The internal node id if found, None otherwise.
        """
        # Try by neo4j_id first
        if node.neo4j_id is not None:
            if self._graph.has_node(node.neo4j_id):
                return node.neo4j_id
            return None

        # Search by primary key
        pk = node._primary_key
        if pk is None:
            return None

        idx_key = self._pk_index_key(node.main_label, pk)
        node_id = self._pk_index.get(idx_key)
        if node_id is not None and self._graph.has_node(node_id):
            return node_id

        for node_id, attrs in self._node_attrs.items():
            # Must match main_label AND all pk fields
            if attrs.get("main_label") == node.main_label and self._attrs_match_pk(attrs, pk):
                return node_id

        return None

    def create(
        self,
        migration: Tuple[List, List],
        update: bool = False,
        replace: bool = False,
    ) -> None:
        """Bulk import nodes and relations.

        Iterates over the node and relation lists, inserting each with
        the given ``update`` and ``replace`` flags.

        Args:
            migration: A tuple ``(node_list, relation_list)``.
            update: Passed to ``insertNode`` / ``insertRelation``.
            replace: Passed to ``insertNode`` / ``insertRelation``.
        """
        node_info, rel_info = migration
        for node in node_info:
            self.insertNode(node, update=update, replace=replace)
        for rel in rel_info:
            self.insertRelation(rel, update=update, replace=replace)

    # ------------------------------------------------------------------
    # Public API: query operations
    # ------------------------------------------------------------------

    def get_node(self, node_id: int) -> Optional[Node]:
        """Retrieve a node by its internal id.

        Args:
            node_id: The internal node id.

        Returns:
            A ``Node`` instance with the stored attributes, or None
            if no node with that id exists.
        """
        if self._graph.has_node(node_id):
            attrs = self._node_attrs.get(node_id, {})
            labels = attrs.get("labels", [])
            main_label = attrs.get("main_label", "")
            pk = attrs.get("pk", {})
            return Node(neo4j_id=node_id, pk=pk, main_label=main_label, alternative_labels=labels)
        return None

    def get_node_ids(self) -> List[int]:
        """Return all internal node ids in the graph."""
        return list(self._graph.nodes())

    def get_nodes(self) -> List[int]:
        """Alias for :meth:`get_node_ids` for backward compatibility."""
        return self.get_node_ids()

    def get_node_pks(self) -> List[Dict[str, Any]]:
        """Return all primary keys of nodes in the graph."""
        result = []
        for nid, pk in self._node_pks.items():
            if nid in self._graph.nodes:
                node = self._graph.nodes[nid]
                label = node.get("main_label", "")
                result.append({"main_label": label, "pk": pk})
        return result

    def get_edges(self) -> List[Tuple[int, int, str]]:
        """Return all edges as ``(src_id, dst_id, rel_type)`` tuples."""
        result = []
        for u, v, key, data in self._graph.edges(data=True, keys=True):
            result.append((u, v, data.get("rel_type", key)))
        return result

    def get_node_attrs(self, node_id: int) -> Optional[Dict[str, Any]]:
        """Return attributes stored for a node.

        Args:
            node_id: The internal node id.

        Returns:
            A dict of node attributes, or None if the node does not exist.
        """
        return self._node_attrs.get(node_id)

    def get_edge_attrs(self, u: int, v: int, key: str) -> Optional[Dict[str, Any]]:
        """Return attributes stored for an edge.

        Args:
            u: Source node id.
            v: Destination node id.
            key: Edge type / key.

        Returns:
            A dict of edge attributes, or None if the edge does not exist.
        """
        return self._edge_attrs.get((u, v, key))

    def find_nodes_by_property(self, prop_name: str, value: Any) -> List[int]:
        """Return node ids indexed by an exact property value match."""
        idx_key = (prop_name, self._normalize_index_value(value))
        return sorted(self._property_index.get(idx_key, set()))

    def find_nodes(self, filters: Dict[str, Any], match: str = "all") -> List[int]:
        """Find node ids by indexed property filters.

        Args:
            filters: Mapping of property name -> exact value.
            match: ``"all"`` (intersection) or ``"any"`` (union).
        """
        if not filters:
            return self.get_nodes()
        if match not in ("all", "any"):
            raise ValueError("match must be 'all' or 'any'")

        buckets: List[Set[int]] = []
        for prop_name, value in filters.items():
            idx_key = (prop_name, self._normalize_index_value(value))
            buckets.append(set(self._property_index.get(idx_key, set())))

        if not buckets:
            return []
        if match == "all":
            result = set.intersection(*buckets)
        else:
            result = set.union(*buckets)
        return sorted(result)

    def debug(self) -> Dict[str, Any]:
        """Return a human-readable snapshot of the graph state.

        Returns a dict with keys:
        - ``nodes``: list of (id, label, pk)
        - ``edges``: list of (src_id, dst_id, rel_type, attrs)
        - ``fk_index``: dict mapping node_id -> list of (other_id, rel_type, direction)
        """
        return {
            "nodes": [
                (nid, attrs.get("main_label"), attrs.get("pk"))
                for nid, attrs in self._node_attrs.items()
            ],
            "edges": [
                (u, v, data.get("rel_type", key), self._edge_attrs.get((u, v, key), {}))
                for u, v, key, data in self._graph.edges(data=True, keys=True)
            ],
            "fk_index": {
                k: list(v) for k, v in self._fk_index.items()
            },
        }

    def print_debug(self) -> None:
        """Print a formatted snapshot of the graph state to stdout."""
        state = self.debug()
        print("\n=== NetworkXGraph State ===")
        print(f"Nodes ({len(state['nodes'])}):")
        for nid, label, pk in state["nodes"]:
            print(f"  [{nid}] {label} pk={pk}")
        print(f"Edges ({len(state['edges'])}):")
        for u, v, rtype, attrs in state["edges"]:
            print(f"  [{u}] --[{rtype}]--> [{v}] attrs={attrs}")
        print(f"FK Index ({len(state['fk_index'])} entries):")
        for nid, entries in state['fk_index'].items():
            print(f"  [{nid}] -> {entries}")
        print("=======================\n")

    # ------------------------------------------------------------------
    # Public API: Query
    # ------------------------------------------------------------------

    def query(
        self,
        filter_dict: Optional[Union[Dict[str, Any], str]] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[Tuple[str, int]] = None,
        limit_val: Optional[int] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Query nodes using MongoDB-style filters or execute Cypher.

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
                Ignored for Cypher queries.
            sort: Tuple of ``(field_name, direction)`` where direction is
                ``1`` (ascending) or ``-1`` (descending).
                Ignored for Cypher queries.
            limit_val: Maximum number of results to return.
                Ignored for Cypher queries.
            params: Optional parameter dict for ``$name`` substitution
                in Cypher queries. Ignored for MongoDB-style queries.

        Returns:
            A list of dicts, one per matching node (MongoDB-style) or
            one per result record (Cypher-style).

        Example:
            >>> # MongoDB-style
            >>> graph.query({"age": {"$gt": 25}})
            >>> # Cypher-style
            >>> graph.query("MATCH (n:Person) RETURN n")
        """
        if isinstance(filter_dict, str):
            # Cypher-style query
            return _execute_cypher(self, filter_dict, params or {})

        # MongoDB-style query
        if filter_dict is None:
            filter_dict = {}

        results: List[Dict[str, Any]] = []
        for nid, attrs in self._node_attrs.items():
            if not _matches_filter(attrs, filter_dict):
                continue
            main_label = attrs.get("main_label", "Node")
            doc: Dict[str, Any] = {
                "node_id": nid,
                "main_label": main_label,
                "properties": dict(attrs),
            }
            if projection:
                doc["properties"] = _apply_projection(
                    dict(doc["properties"]), projection
                )
            results.append(doc)

        if sort is not None:
            field, direction = sort
            if not field.startswith("properties."):
                field = "properties." + field
            results.sort(
                key=lambda d: _sort_key(d, field) or "", reverse=direction == -1
            )

        if limit_val is not None:
            results = results[:limit_val]

        return results

    def query_nodes(
        self,
        filter_dict: Optional[Dict[str, Any]] = None,
    ) -> "NxQuery":
        """Return an :class:`~cvcdocdb.nx_query.NxQuery` fluent builder.

        Unlike :meth:`query`, this returns a lazy, chainable query object
        instead of a list.  Call ``.where(...)``, ``.limit(...)``,
        ``.count()``, ``.to_list()``, or ``.ids()`` to materialise.

        Args:
            filter_dict: Optional initial MongoDB-style filter.

        Returns:
            An ``NxQuery`` instance over all (or filtered) nodes.

        Example:
            >>> (graph.query_nodes()
            ...     .where({"main_label": "Person"})
            ...     .where({"age": {"$gte": 30}})
            ...     .limit(10)
            ...     .to_list())
        """
        from cvcdocdb.nx_query import NxQuery

        items = (
            (nid, attrs)
            for nid, attrs in self._node_attrs.items()
            if filter_dict is None or _match(attrs, filter_dict)
        )
        return NxQuery(self._graph, items, kind="nodes")

    def schema_yaml(self, db_name: str) -> str:
        """Introspect the database and return a YAML schema description.

        Scans ``_node_attrs`` and ``_edge_attrs`` to collect all labels,
        properties, relationship types, and counts.  Generates Python
        class names from labels and relationship types.

        **WeakNode detection**: labels whose PK fields are a proper
        superset of another label's PK fields are inferred as children
        (WeakNode).  The child-specific PK fields are the set difference.

        Args:
            db_name: Human-readable database name (e.g. ``"got"``).

        Returns:
            A YAML string suitable for code generation.
        """
        import datetime  # local import to avoid runtime dep

        # ── Collect labels and their properties ──────────────────────
        label_props: Dict[str, Dict[str, str]] = {}  # label -> {prop: type}
        label_counts: Dict[str, int] = {}

        for nid, attrs in self._node_attrs.items():
            label = attrs.get("main_label", "Node")
            label_counts[label] = label_counts.get(label, 0) + 1
            if label not in label_props:
                label_props[label] = {}
            for k, v in attrs.items():
                if k in ("pk", "main_label", "labels"):
                    continue
                if k not in label_props[label]:
                    label_props[label][k] = self._python_type(v)

        # ── Collect PK fields per label from _node_pks ───────────────
        label_pk_fields: Dict[str, Set[str]] = {}
        for nid, pk in self._node_pks.items():
            if pk is None:
                continue
            label = self._node_attrs.get(nid, {}).get("main_label", "Node")
            if label not in label_pk_fields:
                label_pk_fields[label] = set()
            label_pk_fields[label].update(pk.keys())

        # ── Detect WeakNode hierarchies ──────────────────────────────
        # For each label, find if its PK fields are a proper superset
        # of another label's PK fields → it's a child of that label.
        label_parent: Dict[str, str] = {}       # child -> parent label
        label_child_pk: Dict[str, Set[str]] = {}  # child -> child-specific PK fields

        all_labels = set(label_props.keys())
        for child_label in all_labels:
            child_pks = label_pk_fields.get(child_label, set())
            if not child_pks:
                continue
            best_parent = None
            best_overlap = 0
            for parent_label in all_labels:
                if parent_label == child_label:
                    continue
                parent_pks = label_pk_fields.get(parent_label, set())
                if not parent_pks:
                    continue
                # child_pks is a proper superset of parent_pks
                if child_pks > parent_pks:
                    overlap = len(child_pks & parent_pks)
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_parent = parent_label
            if best_parent is not None:
                label_parent[child_label] = best_parent
                label_child_pk[child_label] = child_pks - label_pk_fields[best_parent]

        # ── Infer parent_relation from edges (for WeakRelation rel_type) ─
        # The WeakNode detection comes from PKs, but we need the actual
        # edge type used between parent and child for the WeakRelation entry.
        label_parent_rel: Dict[str, str] = {}  # child_label -> rel_type
        for u, v, key, data in self._graph.edges(data=True, keys=True):
            rel_type = data.get("rel_type", key)
            src_label = self._node_attrs.get(u, {}).get("main_label", "Node")
            dst_label = self._node_attrs.get(v, {}).get("main_label", "Node")
            # Check if dst is a child of src
            if label_parent.get(dst_label) == src_label:
                label_parent_rel[dst_label] = rel_type
            # Check if src is a child of dst
            if label_parent.get(src_label) == dst_label:
                label_parent_rel[src_label] = rel_type

        # ── Collect relationships ────────────────────────────────────
        rel_types: Dict[str, Dict[str, Any]] = {}
        for u, v, key, data in self._graph.edges(data=True, keys=True):
            rel_type = data.get("rel_type", key)
            if rel_type not in rel_types:
                rel_types[rel_type] = {"src": set(), "dst": set(), "props": {}, "count": 0}
            rel_types[rel_type]["count"] += 1

            # Determine src/dst labels
            src_attrs = self._node_attrs.get(u, {})
            dst_attrs = self._node_attrs.get(v, {})
            src_label = src_attrs.get("main_label", "Node")
            dst_label = dst_attrs.get("main_label", "Node")
            rel_types[rel_type]["src"].add(src_label)
            rel_types[rel_type]["dst"].add(dst_label)

            # Collect edge properties
            for k, val in data.items():
                if k not in ("rel_type", "rel_type"):
                    if k not in rel_types[rel_type]["props"]:
                        rel_types[rel_type]["props"][k] = self._python_type(val)

        # ── Build YAML manually (no PyYAML dependency) ───────────────
        lines: List[str] = []
        lines.append(f"# Schema generated from: {db_name}")
        lines.append(f"# Generated at: {datetime.datetime.now().strftime('%Y-%m-%d')}")
        lines.append("")

        # Labels section
        lines.append("labels:")
        if not label_props:
            lines.append("  {}")
        else:
            for label in sorted(label_props):
                props = label_props[label]
                count = label_counts[label]
                class_name = label[0].upper() + label[1:] if label else "Node"

                # Determine base_class and parent info
                if label in label_parent:
                    base_class = "WeakNode"
                    parent = label_parent[label]
                    child_pks = sorted(label_child_pk.get(label, set()))
                    pk_str = f"[{', '.join(repr(f) for f in child_pks)}]" if child_pks else "[]"
                else:
                    base_class = "Node"
                    # Root label: all PK fields are its own
                    all_pks = sorted(label_pk_fields.get(label, set()))
                    pk_str = f"[{', '.join(repr(f) for f in all_pks[:2])}]" if all_pks else "[]"

                lines.append(f"  {label}:")
                lines.append(f"    class_name: {class_name}")
                lines.append(f"    base_class: {base_class}")
                if label in label_parent:
                    lines.append(f"    parent: {parent}")
                    lines.append(f"    parent_relation: {label_parent_rel.get(label, 'HAS')}")
                lines.append(f"    properties:")
                if not props:
                    lines.append(f"      {{}}")
                else:
                    for prop_name, prop_type in sorted(props.items()):
                        lines.append(f"      {prop_name}: {prop_type}")
                lines.append(f"    primary_key: {pk_str}")
                lines.append(f"    count: {count}")

        lines.append("")

        # Relationships section (regular relations only)
        lines.append("relationships:")
        if not rel_types:
            lines.append("  {}")
        else:
            for rel_type in sorted(rel_types):
                info = rel_types[rel_type]
                class_name = rel_type[0].upper() + rel_type[1:] if rel_type else "Rel"
                # Convert to camelCase for class name
                class_name = "".join(w.capitalize() for w in rel_type.lower().split("_"))
                srcs = sorted(info["src"])
                dsts = sorted(info["dst"])
                props = info["props"]

                lines.append(f"  {rel_type}:")
                lines.append(f"    class_name: {class_name}")
                lines.append(f"    src: {', '.join(srcs)}")
                lines.append(f"    dst: {', '.join(dsts)}")
                lines.append(f"    properties:")
                if not props:
                    lines.append(f"      {{}}")
                else:
                    for prop_name, prop_type in sorted(props.items()):
                        lines.append(f"      {prop_name}: {prop_type}")
                lines.append(f"    count: {info['count']}")

        # WeakRelations section (inferred from WeakNode PK hierarchies)
        # If label A is a WeakNode child of label B, the relationship
        # between them is a WeakRelation with _propagate=true.
        lines.append("")
        lines.append("weak_relations:")
        if not label_parent:
            lines.append("  {}")
        else:
            for child_label, rel_type in sorted(label_parent_rel.items()):
                parent = label_parent[child_label]
                class_name = "".join(w.capitalize() for w in rel_type.lower().split("_"))
                lines.append(f"  {rel_type}:")
                lines.append(f"    class_name: {class_name}")
                lines.append(f"    base_class: WeakRelation")
                lines.append(f"    propagate: true")
                lines.append(f"    src: {parent}")
                lines.append(f"    dst: {child_label}")

        return "\n".join(lines) + "\n"

    def _python_type(self, value: Any) -> str:
        """Map a Python value to a YAML/Python type string."""
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "float"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "array"
        if isinstance(value, dict):
            return "object"
        if value is None:
            return "null"
        return "string"

    def count(
        self, filter_dict: Optional[Dict[str, Any]] = None
    ) -> int:
        """Count nodes matching the filter dict.

        Args:
            filter_dict: Dict of field/value pairs to match.

        Returns:
            Integer count of matching nodes.
        """
        if filter_dict is None:
            filter_dict = {}
        return sum(
            1
            for nid, attrs in self._node_attrs.items()
            if _matches_filter(attrs, filter_dict)
        )

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

        Takes a snapshot of the graph state before any modifications.
        If any insertion fails, the snapshot is restored so the graph
        is left unchanged.

        Args:
            strong_node: The root (non-weak) node of the group.
            weak_nodes: Optional list of WeakNode instances.
            weak_relations: Optional list of WeakRelation instances.

        Returns:
            The internal id of the ``strong_node``.

        Raises:
            RuntimeError: If any part of the group creation fails.
        """
        with self._guarded_write():
            # Snapshot current state for rollback on failure
            snapshot = self._snapshot()

            try:
                # 1. Insert the strong node
                strong_id = self.insertNode(strong_node, insert_parent=False, update=False, replace=False)

                # 2. Insert weak nodes (they reference the strong node as parent)
                if weak_nodes:
                    for wn in weak_nodes:
                        self.insertNode(wn, insert_parent=True, update=False, replace=False)

                # 3. Insert weak relations
                if weak_relations:
                    for wr in weak_relations:
                        self.insertRelation(wr, update=False, replace=False)

                # 4. Mark the strong node as having its weak children initialized.
                #    The edges already carry _propagate=True from insertNode, so
                #    init_propagation() will detect the weak nodes without issue.
                strong_attrs = self._node_attrs.get(strong_id, {})
                strong_attrs["_weak_init_done"] = True
                self._node_attrs[strong_id] = strong_attrs
                self._graph.nodes[strong_id]["_weak_init_done"] = True

                return strong_id

            except Exception:
                # Rollback: restore snapshot
                self._restore(snapshot)
                raise

    def _get_group_nodes(self) -> Set[int]:
        """Return the set of node ids that belong to the current group
        (strong node + weak nodes). Used for targeted rollback."""
        return set()

    # ------------------------------------------------------------------
    # Propagation property initialization
    # ------------------------------------------------------------------

    def init_propagation(
        self,
        background: bool = False,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """Scan the backend graph and initialize propagation properties.

        See GraphStore.init_propagation for details.
        """
        with self._propagation_lock:
            if self._propagation_initialized:
                return False
            self._propagation_initialized = True

        def _run():
            with self._guarded_write():
                # Collect all node ids
                all_ids = list(self._node_attrs.keys())
                total = len(all_ids)

                # First pass: identify weak nodes by scanning edges for _propagate
                weak_node_ids: Set[int] = set()
                node_parent_relation: Dict[int, str] = {}

                for (u, v, key), edge_attrs in self._edge_attrs.items():
                    if edge_attrs.get("_propagate"):
                        weak_node_ids.add(v)
                        node_parent_relation[v] = key

                # Second pass: set properties on nodes
                for idx, nid in enumerate(all_ids):
                    attrs = self._node_attrs.get(nid, {})

                    # Skip strong nodes whose weak children are already initialized.
                    # _weak_init_done=True means create_group() created this node
                    # together with its WeakNodes (edges carry _propagate=True).
                    if attrs.get("_weak_init_done"):
                        continue

                    # Mark weak nodes
                    if nid in weak_node_ids:
                        if "is_weak" not in attrs:
                            attrs["is_weak"] = True
                            self._node_attrs[nid] = attrs
                            self._graph.nodes[nid]["is_weak"] = True

                        # Also set _propagate flag on the node
                        if "_propagate" not in attrs:
                            attrs["_propagate"] = True
                            self._node_attrs[nid] = attrs
                            self._graph.nodes[nid]["_propagate"] = True

                    # Set parent_relation on weak nodes
                    if nid in node_parent_relation and "parent_relation" not in attrs:
                        attrs["parent_relation"] = node_parent_relation[nid]
                        self._node_attrs[nid] = attrs
                        self._graph.nodes[nid]["parent_relation"] = node_parent_relation[nid]

                    # Check for dependency edges (HAS_*)
                    deps = {}
                    for (u2, v2, key2), edge_attrs2 in self._edge_attrs.items():
                        if u2 == nid and key2.startswith("HAS_"):
                            deps[key2[4:].lower()] = {
                                "main_label": self._node_attrs.get(v2, {}).get("main_label", ""),
                                "pk": self._node_attrs.get(v2, {}).get("pk", {}),
                            }
                    if deps and "_dependencies" not in attrs:
                        attrs["_dependencies"] = deps
                        self._node_attrs[nid] = attrs
                        self._graph.nodes[nid]["_dependencies"] = deps

                    # Progress callback
                    if progress_callback and (idx % 100 == 0 or idx == total - 1):
                        progress_callback(idx + 1, total)

        if background:
            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            return True
        else:
            _run()
            return True

    # ------------------------------------------------------------------
    # Public API: lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release resources and clear the graph.

        Does NOT persist anything itself — every mutating method
        (``insertNode``/``insertRelation``/``deleteNode``/``init_propagation``/
        ``create_group``/``enable_vector_index``) already reloads the latest
        on-disk state and saves as part of a cross-process file-locked
        load-mutate-save cycle (see ``_guarded_write``), so by the time
        ``close()`` runs the on-disk state already reflects every change
        made through this instance — including any other process's writes
        that happened in between.

        This used to call ``_save_state()`` unconditionally, which re-wrote
        the *entire* file with whatever was loaded into memory when this
        instance was opened — including for an instance that only ever did
        reads. Two overlapping opens of the same persistence path (e.g. a
        slow read in one process/request and a write in another) would race:
        whichever instance closed last silently overwrote the other's writes
        with its own (possibly stale) snapshot, even though it never mutated
        anything itself. Removing the call here means a read-only instance
        can never clobber someone else's concurrent write on close. The
        remaining "two writers" race (both mutate, neither just reads) is
        handled separately by ``_guarded_write``'s reload-before-mutate
        under a cross-process lock.
        """
        self._graph.clear()
        self._node_attrs.clear()
        self._edge_attrs.clear()
        self._fk_index.clear()
        self._pk_index.clear()
        self._property_index.clear()
        self._vector_indexes.clear()
        self._vector_index_meta.clear()

    def enable_vector_index(
        self,
        property_name: str,
        dimensions: int,
        space: str = "cosine",
        ef_construction: int = 200,
        m: int = 16,
    ) -> None:
        """Enable ANN vector indexing for a given node property.

        The property values must be 1D vectors with exactly ``dimensions`` items.
        """
        if hnswlib is None:
            raise RuntimeError("hnswlib is required for vector indexing")
        if dimensions <= 0:
            raise ValueError("dimensions must be > 0")
        if space not in ("cosine", "l2", "ip"):
            raise ValueError("space must be one of: cosine, l2, ip")

        with self._guarded_write():
            current_meta = self._vector_index_meta.get(property_name)
            if current_meta is not None:
                if current_meta["dimensions"] != dimensions or current_meta["space"] != space:
                    raise ValueError(
                        f"vector index for '{property_name}' already exists with "
                        f"space={current_meta['space']} dim={current_meta['dimensions']}"
                    )
                return

            self._create_empty_vector_index(
                property_name=property_name,
                dimensions=dimensions,
                space=space,
                ef_construction=ef_construction,
                m=m,
                max_elements=max(1024, len(self._node_attrs) + 128),
            )
            self._rebuild_vector_index(property_name)

    def query_vector_index(
        self,
        property_name: str,
        vector: Union[List[float], Tuple[float, ...], np.ndarray],
        top_k: int = 10,
    ) -> List[Tuple[int, float]]:
        """Query nearest neighbors by vector similarity for one indexed property."""
        if top_k <= 0:
            return []
        if property_name not in self._vector_indexes:
            raise ValueError(f"vector index for property '{property_name}' is not enabled")

        meta = self._vector_index_meta[property_name]
        label_to_node = meta["label_to_node"]
        if not label_to_node:
            return []

        query_vec = self._coerce_vector(vector, meta["dimensions"])
        query_k = min(top_k, len(label_to_node))
        labels, distances = self._vector_indexes[property_name].knn_query(query_vec.reshape(1, -1), k=query_k)

        results: List[Tuple[int, float]] = []
        for label, dist in zip(labels[0], distances[0]):
            node_id = label_to_node.get(int(label))
            if node_id is not None:
                results.append((node_id, float(dist)))
        return results

    # ------------------------------------------------------------------
    # Protected helpers: node operations
    # ------------------------------------------------------------------

    def _ensure_node_inserted(
        self,
        node: Node,
        update: bool = False,
        replace: bool = False,
    ) -> int:
        """Ensure the node exists, returning its id.

        Mirrors Neo4jGraph._insertNode semantics:

        - ``update=True``: MERGE + SET — adds/updates attributes without
          deleting the node.  The node keeps its existing id.
        - ``update=False`` + ``replace=True``: deletes the existing node
          (detach) and creates a fresh one with a new id.
        - ``update=False`` + ``replace=False``: tries to CREATE a new
          node.  If a node with the same PK already exists this is a
          duplicate-key error (raises ``RuntimeError``).
        """
        # For WeakNodes whose PK was backend-generated (pk=None originally),
        # the PK equals the parent's PK. These nodes should never be treated
        # as duplicates — each gets a unique backend-assigned ID.
        is_backend_assigned_pk = (
            node["is_weak"]
            and node._parent is not None
            and node._parent._primary_key is not None
            and node._primary_key == node._parent._primary_key
        )

        existing = self.checkNode(node) if not is_backend_assigned_pk else None

        if existing is not None:
            # Node already exists
            if replace:
                # ON UPDATE CASCADE: in Neo4j, edges reference nodes
                # by internal ID which never changes.  replace=True
                # deletes the node (with detach, cascading edges) and
                # creates a fresh one — the caller must recreate
                # relations if needed.
                self.deleteNode(node, detach=True, propagation=True)
                self._node_counter += 1
                node_id = self._node_counter
            elif update:
                # ON UPDATE CASCADE: PK changes are safe — the FK index is
                # keyed by internal node IDs which never change.  The MERGE
                # + SET in _ensure_node_inserted already updates the PK in
                # _node_attrs, so all relations continue to resolve correctly.
                pass
                # MERGE + SET: merge attributes into existing node
                node_id = existing
                old_attrs = self._node_attrs.get(node_id, {}).copy()
                pk, attributes = node.attributes
                props = attributes if pk is None else {**pk, **attributes}
                self._node_attrs[node_id].update(props)
                self._graph.nodes[node_id].update(props)
                self._deindex_node(node_id, old_attrs)
                self._index_node(node_id, self._node_attrs[node_id])
                self._sync_vector_indexes_for_node(node_id, self._node_attrs[node_id])
                node.neo4j_id = node_id
                return node_id
            else:
                # Duplicate key — same as Neo4j ConstraintError
                raise RuntimeError(
                    f"Duplicate key: node with pk={node._primary_key} "
                    f"and main_label={node.main_label} already exists (id={existing}). "
                    f"Use replace=True to overwrite or update=True to merge attributes."
                )
        else:
            # Node does not exist — create fresh
            self._node_counter += 1
            node_id = self._node_counter

        # Si el node tenia pk=None explícit, assignem l'ID generat com a PK
        if node._primary_key is None:
            node._primary_key = {"id": node_id}

        # For WeakNodes whose PK was backend-generated (pk=None originally),
        # replace the inherited parent PK with the backend-assigned ID
        if is_backend_assigned_pk:
            node._primary_key = {"id": node_id}

        # Store node attributes
        pk, attributes = node.attributes
        props = attributes if pk is None else {**pk, **attributes}
        self._node_attrs[node_id] = {
            "pk": pk,
            "main_label": node.main_label,
            "labels": node.labels,
            **props,
        }
        self._index_node(node_id, self._node_attrs[node_id])
        self._sync_vector_indexes_for_node(node_id, self._node_attrs[node_id])

        self._graph.add_node(node_id, **props)
        return node_id

    def _resolve_node_id(self, node_data: Union[Node, Dict[str, Any]]) -> Optional[int]:
        """Resolve a node or node dict to its internal graph id."""
        if isinstance(node_data, Node):
            if node_data.neo4j_id is not None and self._graph.has_node(node_data.neo4j_id):
                return node_data.neo4j_id
            return self.checkNode(node_data)

        if isinstance(node_data, dict):
            nid = node_data.get("neo4j_id")
            if nid is not None and self._graph.has_node(nid):
                return nid
            # Try to find by pk
            pk = node_data.get("pk")
            if pk and isinstance(pk, dict):
                main_label = node_data.get("main_label")
                idx_key = self._pk_index_key(main_label, pk)
                idx_id = self._pk_index.get(idx_key)
                if idx_id is not None and self._graph.has_node(idx_id):
                    return idx_id
                for node_id, attrs in self._node_attrs.items():
                    if attrs.get("main_label") == main_label and self._attrs_match_pk(attrs, pk):
                        return node_id

        return None

    # ------------------------------------------------------------------
    # Protected helpers: FK index operations
    # ------------------------------------------------------------------

    def _add_to_fk_index(self, src_id: int, dst_id: int, edge_key: str) -> None:
        """Record a new edge in the FK index for both endpoints."""
        self._fk_index.setdefault(src_id, set()).add((dst_id, edge_key, "out"))
        self._fk_index.setdefault(dst_id, set()).add((src_id, edge_key, "in"))

    def _remove_from_fk_index(self, src_id: int, dst_id: int, edge_key: str) -> None:
        """Remove an edge from the FK index."""
        if src_id in self._fk_index:
            self._fk_index[src_id].discard((dst_id, edge_key, "out"))
            if not self._fk_index[src_id]:
                del self._fk_index[src_id]
        if dst_id in self._fk_index:
            self._fk_index[dst_id].discard((src_id, edge_key, "in"))
            if not self._fk_index[dst_id]:
                del self._fk_index[dst_id]

    def _clean_fk_index(self, node_id: int) -> None:
        """Remove all FK index entries for a node being deleted."""
        if node_id in self._fk_index:
            for other_id, edge_key, direction in self._fk_index[node_id]:
                if other_id in self._fk_index:
                    if direction == "out":
                        self._fk_index[other_id].discard((node_id, edge_key, "in"))
                    else:
                        self._fk_index[other_id].discard((node_id, edge_key, "out"))
                    if not self._fk_index[other_id]:
                        del self._fk_index[other_id]
            del self._fk_index[node_id]

    def _cascade_delete(self, node_id: int) -> None:
        """ON DELETE CASCADE: delete node and all connected edges.

        Uses the FK index to find all edges connected to the node (both
        incoming and outgoing) and removes them.  Neighbor nodes are left
        as standalone entities.  Mirrors Neo4jGraph._cascade_delete.
        """
        entries = self._fk_index.get(node_id, set()).copy()
        for neighbor_id, rel_type, direction in entries:
            # Delete the edge in the correct direction
            if direction == "out":
                # Edge: node_id → neighbor_id
                if self._graph.has_edge(node_id, neighbor_id, key=rel_type):
                    self._graph.remove_edge(node_id, neighbor_id, key=rel_type)
                    self._edge_attrs.pop((node_id, neighbor_id, rel_type), None)
            else:
                # Edge: neighbor_id → node_id
                if self._graph.has_edge(neighbor_id, node_id, key=rel_type):
                    self._graph.remove_edge(neighbor_id, node_id, key=rel_type)
                    self._edge_attrs.pop((neighbor_id, node_id, rel_type), None)

            # Clean FK index for neighbor
            if neighbor_id in self._fk_index:
                self._fk_index[neighbor_id] = {
                    e for e in self._fk_index[neighbor_id]
                    if e != (node_id, rel_type, "in" if direction == "out" else "out")
                }
                if not self._fk_index[neighbor_id]:
                    del self._fk_index[neighbor_id]
        self._fk_index.pop(node_id, None)
        old_attrs = self._node_attrs.pop(node_id, None)
        if old_attrs is not None:
            self._deindex_node(node_id, old_attrs)

    def _set_null_delete(self, node_id: int) -> None:
        """ON DELETE SET NULL: delete node, remove connected edges, keep neighbors.

        Unlike CASCADE, this does NOT recurse into neighbor nodes.
        Edges are removed (required by the graph model), but neighbor
        nodes remain as standalone entities.
        """
        self._clean_fk_index(node_id)
        self._remove_node_attrs(node_id)

    def _remove_node_attrs(self, node_id: int) -> None:
        """Remove internal bookkeeping for a node."""
        old_attrs = self._node_attrs.pop(node_id, None)
        if old_attrs is not None:
            self._deindex_node(node_id, old_attrs)
        self._edge_attrs = {
            k: v for k, v in self._edge_attrs.items()
            if k[0] != node_id and k[1] != node_id
        }

    def _reconnect_edges_for_node(self, old_id: int, new_id: int) -> None:
        """ON UPDATE CASCADE for relations: rewire edges from old_id to new_id.

        Edges where old_id is src or dst are redirected to new_id.
        Edges where old_id is in the FK index are also updated.
        """
        # Reconnect edges in the graph where old_id is src
        for neighbor, key, data in list(self._graph.out_edges(old_id, keys=True, data=True)):
            self._graph.remove_edge(old_id, neighbor, key=key)
            self._graph.add_edge(new_id, neighbor, key=key, rel_type=data.get("rel_type", key))
            # Move edge attrs
            attrs = self._edge_attrs.pop((old_id, neighbor, key), {})
            if attrs:
                self._edge_attrs[(new_id, neighbor, key)] = attrs

        # Reconnect edges in the graph where old_id is dst
        for pred, neighbor, key, data in list(self._graph.in_edges(old_id, keys=True, data=True)):
            self._graph.remove_edge(pred, old_id, key=key)
            self._graph.add_edge(pred, new_id, key=key, rel_type=data.get("rel_type", key))
            attrs = self._edge_attrs.pop((pred, old_id, key), {})
            if attrs:
                self._edge_attrs[(pred, new_id, key)] = attrs

        # Update FK index: redirect all entries pointing to old_id
        for other_id in list(self._fk_index):
            self._fk_index[other_id] = {
                (new_id if oid == old_id else oid, rt, d)
                for oid, rt, d in self._fk_index[other_id]
            }
        # Move old_id entries to new_id in FK index
        if old_id in self._fk_index:
            self._fk_index[new_id] = self._fk_index.pop(old_id)

    # ------------------------------------------------------------------
    # Protected helpers: snapshot / rollback for create_group
    # ------------------------------------------------------------------

    def _snapshot(self) -> Dict[str, Any]:
        """Capture the current graph state for rollback on failure."""
        import copy
        return {
            "graph_edges": list(self._graph.edges(data=True, keys=True)),
            "graph_nodes": list(self._graph.nodes(data=True)),
            "node_attrs": copy.deepcopy(self._node_attrs),
            "edge_attrs": copy.deepcopy(self._edge_attrs),
            "fk_index": copy.deepcopy(self._fk_index),
            "pk_index": copy.deepcopy(self._pk_index),
            "property_index": copy.deepcopy(self._property_index),
            "node_pks": copy.deepcopy(self._node_pks),
            "node_counter": self._node_counter,
        }

    def _restore(self, snap: Dict[str, Any]) -> None:
        """Restore graph state from a snapshot (rollback)."""
        import copy
        import networkx as nx

        self._graph = nx.MultiDiGraph()
        for nid, data in snap["graph_nodes"]:
            self._graph.add_node(nid, **data)
        for u, v, key, data in snap["graph_edges"]:
            self._graph.add_edge(u, v, key=key, **data)
        self._node_attrs = snap["node_attrs"]
        self._edge_attrs = snap["edge_attrs"]
        self._fk_index = snap["fk_index"]
        self._pk_index = snap["pk_index"]
        self._property_index = snap["property_index"]
        self._node_pks = snap["node_pks"]
        self._node_counter = snap["node_counter"]

    # ------------------------------------------------------------------
    # Protected helpers: persistence
    # ------------------------------------------------------------------

    @contextmanager
    def _guarded_write(self) -> Iterator[None]:
        """Serialize a load-mutate-save cycle across processes/instances.

        Every public mutating method wraps its body in this context
        manager instead of calling ``_save_state()`` directly. On the
        *outermost* call it: acquires the cross-process file lock, reloads
        the latest on-disk state (so the mutation is applied on top of
        whatever another process already persisted, not a stale snapshot
        from whenever this instance was constructed or last reloaded), lets
        the body mutate, and saves before releasing the lock.

        Without this, two ``NetworkXGraph`` instances (in one process or
        several) pointing at the same ``persistence_path`` each hold an
        independent in-memory copy: whichever one saves last silently wipes
        out any change the other already persisted — a classic lost
        update. Reloading under a held lock immediately before mutating
        closes that window.

        Reentrant by design: nested calls from the same top-level mutation
        (``create_group()`` calling ``insertNode()``/``insertRelation()``,
        or ``deleteNode()``'s cascade recursion) detect that the lock is
        already held and skip the reload/save — only the outermost call
        does it. This matters, not just as an optimization: a nested
        reload would discard the outer call's own not-yet-saved mutations,
        and a nested save would persist a group's changes as several
        partial writes instead of one atomic one. On an exception, the
        lock is still released (via ``finally``) but nothing is saved, so
        a failed multi-step operation (e.g. ``create_group()``'s rollback)
        leaves the on-disk state untouched rather than reflecting a
        partial write.
        """
        is_outermost = not self._file_lock.is_locked
        self._file_lock.acquire()
        try:
            if is_outermost:
                self._load_state()
            yield
            if is_outermost:
                self._save_state()
        finally:
            self._file_lock.release()

    def _default_persistence_path(self) -> str:
        """Build a deterministic persistence path for the current workspace.

        Uses a per-user, permission-restricted cache directory rather than
        the shared system temp dir: a predictable filename inside a
        world-writable temp dir would let another local user pre-plant a
        malicious pickle file for us to unpickle (see _load_state).
        """
        workspace = os.path.abspath(os.getcwd())
        workspace_hash = hashlib.sha1(workspace.encode("utf-8")).hexdigest()[:12]
        filename = f"cvcdocdb_networkx_graph_{workspace_hash}.pkl"
        cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "cvcdocdb")
        os.makedirs(cache_dir, mode=0o700, exist_ok=True)
        if hasattr(os, "chmod"):
            try:
                os.chmod(cache_dir, 0o700)
            except OSError:
                pass
        return os.path.join(cache_dir, filename)

    def _save_state(self) -> None:
        """Persist the full graph store state atomically."""
        for prop_name, vector_index in self._vector_indexes.items():
            vector_index.save_index(self._vector_index_file(prop_name))

        state = {
            "graph": self._graph,
            "node_attrs": self._node_attrs,
            "edge_attrs": self._edge_attrs,
            "node_counter": self._node_counter,
            "version": self._version,
            "fk_index": self._fk_index,
            "pk_index": self._pk_index,
            "property_index": self._property_index,
            "vector_index_meta": self._vector_index_meta,
        }
        parent_dir = os.path.dirname(self._persistence_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        tmp_path = f"{self._persistence_path}.tmp"
        with open(tmp_path, "wb") as fh:
            pickle.dump(state, fh, protocol=pickle.HIGHEST_PROTOCOL)
        # os.replace can fail on Windows if the destination is locked.
        # Fall back to a non-atomic overwrite in that case.
        try:
            os.replace(tmp_path, self._persistence_path)
        except OSError:
            if os.path.exists(self._persistence_path):
                os.remove(self._persistence_path)
            os.rename(tmp_path, self._persistence_path)

    def _load_state(self) -> None:
        """Load graph store state from disk if present.

        Refuses to unpickle a file that isn't owned by the current user —
        defense in depth against a malicious pickle pre-planted at a
        predictable path by another local user (see also
        _default_persistence_path, which avoids the shared temp dir).
        """
        if not os.path.exists(self._persistence_path):
            return
        if hasattr(os, "getuid"):
            file_uid = os.stat(self._persistence_path).st_uid
            if file_uid != os.getuid():
                raise RuntimeError(
                    f"Refusing to load {self._persistence_path!r}: "
                    f"it is not owned by the current user (uid {os.getuid()}, "
                    f"file owned by uid {file_uid})."
                )
        with open(self._persistence_path, "rb") as fh:
            state = pickle.load(fh)
        self._graph = state.get("graph", nx.MultiDiGraph())
        self._node_attrs = state.get("node_attrs", {})
        self._edge_attrs = state.get("edge_attrs", {})
        self._node_counter = state.get("node_counter", 0)
        self._version = state.get("version", 0.0)
        self._fk_index = state.get("fk_index", {})
        self._pk_index = state.get("pk_index", {})
        self._property_index = state.get("property_index", {})
        self._vector_index_meta = state.get("vector_index_meta", {})
        # Backward compatibility with older persistence files without indexes.
        if not self._pk_index and self._node_attrs:
            self._rebuild_indexes()

        self._vector_indexes = {}
        if self._vector_index_meta:
            if hnswlib is None:
                raise RuntimeError("hnswlib is required to load persisted vector indexes")
            for prop_name, meta in self._vector_index_meta.items():
                idx = hnswlib.Index(space=meta["space"], dim=meta["dimensions"])
                vector_path = self._vector_index_file(prop_name)
                if os.path.exists(vector_path):
                    idx.load_index(vector_path, max_elements=meta.get("max_elements", 0))
                    idx.set_ef(max(50, meta.get("ef_construction", 200)))
                    self._vector_indexes[prop_name] = idx
                else:
                    self._create_empty_vector_index(
                        property_name=prop_name,
                        dimensions=meta["dimensions"],
                        space=meta["space"],
                        ef_construction=meta.get("ef_construction", 200),
                        m=meta.get("m", 16),
                        max_elements=max(1024, len(self._node_attrs) + 128),
                    )
                    self._rebuild_vector_index(prop_name)

    # ------------------------------------------------------------------
    # Protected helpers: indexing
    # ------------------------------------------------------------------

    def _index_node(self, node_id: int, attrs: Dict[str, Any]) -> None:
        """Index a node by PK and by scalar properties."""
        pk = attrs.get("pk")
        pk_key = self._pk_index_key(str(attrs.get("main_label", "")), pk if isinstance(pk, dict) else None)
        if pk_key is not None:
            existing = self._pk_index.get(pk_key)
            if existing is not None and existing != node_id:
                raise RuntimeError(
                    f"Duplicate PK: node with pk={pk} "
                    f"and main_label={attrs.get('main_label')} already exists (id={existing})."
                )
            self._pk_index[pk_key] = node_id

        for prop_name, prop_value in attrs.items():
            if prop_name == "pk":
                continue
            idx_key = (prop_name, self._normalize_index_value(prop_value))
            self._property_index.setdefault(idx_key, set()).add(node_id)

    def _deindex_node(self, node_id: int, attrs: Dict[str, Any]) -> None:
        """Remove a node from PK and property indexes."""
        pk = attrs.get("pk")
        pk_key = self._pk_index_key(str(attrs.get("main_label", "")), pk if isinstance(pk, dict) else None)
        if pk_key is not None and self._pk_index.get(pk_key) == node_id:
            del self._pk_index[pk_key]

        for prop_name, prop_value in attrs.items():
            if prop_name == "pk":
                continue
            idx_key = (prop_name, self._normalize_index_value(prop_value))
            node_set = self._property_index.get(idx_key)
            if node_set is None:
                continue
            node_set.discard(node_id)
            if not node_set:
                del self._property_index[idx_key]

        self._remove_node_from_vector_indexes(node_id)

    def _rebuild_indexes(self) -> None:
        """Rebuild all secondary indexes from node attributes."""
        self._pk_index.clear()
        self._property_index.clear()
        for node_id, attrs in self._node_attrs.items():
            self._index_node(node_id, attrs)

    def _normalize_index_value(self, value: Any) -> Any:
        """Convert arbitrary values to a deterministic hashable representation."""
        if isinstance(value, dict):
            return tuple(sorted((str(k), self._normalize_index_value(v)) for k, v in value.items()))
        if isinstance(value, (list, tuple)):
            return tuple(self._normalize_index_value(v) for v in value)
        if isinstance(value, set):
            normalized = [self._normalize_index_value(v) for v in value]
            return tuple(sorted(normalized, key=repr))
        return value

    def _pk_index_key(self, main_label: str, pk: Optional[Dict[str, Any]]) -> Optional[Tuple[str, Tuple[Tuple[str, str], ...]]]:
        """Build the unique PK index key for a node."""
        if pk is None:
            return None
        items = tuple(sorted((str(k), str(v)) for k, v in pk.items()))
        return (str(main_label), items)

    def _attrs_match_pk(self, attrs: Dict[str, Any], pk: Dict[str, Any]) -> bool:
        """Check if node attributes match a primary key dict."""
        for k, v in pk.items():
            attr_val = attrs.get(k)
            if attr_val is None:
                return False
            # Compare as strings to handle int/str mismatches
            if str(attr_val) != str(v):
                return False
        return True

    # ------------------------------------------------------------------
    # Protected helpers: vector index operations
    # ------------------------------------------------------------------

    def _vector_index_file(self, property_name: str) -> str:
        """Return the on-disk file path for one vector index."""
        digest = hashlib.sha1(property_name.encode("utf-8")).hexdigest()[:12]
        return f"{self._persistence_path}.vectors.{digest}.bin"

    def _coerce_vector(
        self,
        value: Union[List[float], Tuple[float, ...], np.ndarray, Any],
        dimensions: int,
    ) -> np.ndarray:
        """Validate and convert a value into a float32 dense vector."""
        vec = np.asarray(value, dtype=np.float32)
        if vec.ndim != 1 or vec.shape[0] != dimensions:
            raise ValueError(f"expected 1D vector of length {dimensions}, got shape {vec.shape}")
        if not np.all(np.isfinite(vec)):
            raise ValueError("vector contains non-finite values")
        return vec

    def _create_empty_vector_index(
        self,
        property_name: str,
        dimensions: int,
        space: str,
        ef_construction: int,
        m: int,
        max_elements: int,
    ) -> None:
        """Create and register an empty hnswlib index for one property."""
        if hnswlib is None:
            raise RuntimeError("hnswlib is required for vector indexing")

        idx = hnswlib.Index(space=space, dim=dimensions)
        idx.init_index(
            max_elements=max_elements,
            ef_construction=ef_construction,
            M=m,
            allow_replace_deleted=True,
        )
        idx.set_ef(max(50, ef_construction))

        self._vector_indexes[property_name] = idx
        self._vector_index_meta[property_name] = {
            "space": space,
            "dimensions": dimensions,
            "ef_construction": ef_construction,
            "m": m,
            "max_elements": max_elements,
            "next_label": 0,
            "label_to_node": {},
            "node_to_label": {},
        }

    def _ensure_vector_capacity(self, property_name: str, required: int) -> None:
        """Resize an hnswlib index if we exceed allocated elements."""
        meta = self._vector_index_meta[property_name]
        current_max = int(meta.get("max_elements", 0))
        if required <= current_max:
            return
        new_max = max(required, max(1024, current_max * 2))
        self._vector_indexes[property_name].resize_index(new_max)
        meta["max_elements"] = new_max

    def _sync_vector_index_for_node(self, property_name: str, node_id: int, attrs: Dict[str, Any]) -> None:
        """Insert/update one node vector inside one vector index."""
        if property_name not in self._vector_indexes:
            return

        meta = self._vector_index_meta[property_name]
        node_to_label = meta["node_to_label"]
        label_to_node = meta["label_to_node"]

        if property_name not in attrs:
            if node_id in node_to_label:
                self._rebuild_vector_index(property_name)
            return

        vector = self._coerce_vector(attrs[property_name], meta["dimensions"])

        label = node_to_label.get(node_id)
        if label is None:
            label = int(meta["next_label"])
            meta["next_label"] = label + 1
            self._ensure_vector_capacity(property_name, len(label_to_node) + 1)
            self._vector_indexes[property_name].add_items(vector.reshape(1, -1), np.array([label], dtype=np.int64))
            node_to_label[node_id] = label
            label_to_node[label] = node_id
            return

        self._vector_indexes[property_name].add_items(vector.reshape(1, -1), np.array([label], dtype=np.int64))

    def _sync_vector_indexes_for_node(self, node_id: int, attrs: Dict[str, Any]) -> None:
        """Insert/update one node across all enabled vector indexes."""
        for property_name in list(self._vector_indexes.keys()):
            self._sync_vector_index_for_node(property_name, node_id, attrs)

    def _remove_node_from_vector_indexes(self, node_id: int) -> None:
        """Remove one node from vector indexes by rebuilding affected indexes."""
        affected = [
            prop_name
            for prop_name, meta in self._vector_index_meta.items()
            if node_id in meta.get("node_to_label", {})
        ]
        for prop_name in affected:
            self._rebuild_vector_index(prop_name)

    def _rebuild_vector_index(self, property_name: str) -> None:
        """Rebuild one vector index from current node attributes."""
        if property_name not in self._vector_index_meta:
            return

        meta = self._vector_index_meta[property_name]
        self._create_empty_vector_index(
            property_name=property_name,
            dimensions=int(meta["dimensions"]),
            space=str(meta["space"]),
            ef_construction=int(meta.get("ef_construction", 200)),
            m=int(meta.get("m", 16)),
            max_elements=max(1024, len(self._node_attrs) + 128),
        )
        for node_id, attrs in self._node_attrs.items():
            self._sync_vector_index_for_node(property_name, node_id, attrs)



# ======================================================================
# MongoDB-style filter functions for NetworkXGraph.query()
# ======================================================================

import operator as _operator
import re as _re
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Operators — copied from nx_query.py with bug fixes
# ---------------------------------------------------------------------------


def _regex_match(v: Any, pattern: str) -> bool:
    """Match *pattern* against *str(v)*, returning False on error.

    Invalid regex patterns (e.g. ``"[invalid"``) return ``False``
    instead of raising ``re.error``.
    """
    if v is None:
        return False
    try:
        return bool(_re.search(pattern, str(v)))
    except _re.error:
        return False


_OPS: Dict[str, Callable[[Any, Any], bool]] = {
    "$eq":  _operator.eq,
    "$ne":  _operator.ne,
    "$gt":  _operator.gt,
    "$gte": _operator.ge,
    "$lt":  _operator.lt,
    "$lte": _operator.le,
    "$in":  lambda v, arr: v in arr,
    "$nin": lambda v, arr: v not in arr,
    "$exists": lambda v, flag: (v is not None) == flag,
    "$regex": _regex_match,
    "$contains": lambda v, item: item in v if isinstance(v, (list, tuple, str)) else False,
}


def _match(attrs: Dict[str, Any], query: Dict[str, Any]) -> bool:
    """Check if node attributes match a MongoDB-style query dict.

    Supports operators: ``$eq``, ``$ne``, ``$gt``, ``$gte``, ``$lt``,
    ``$lte``, ``$in``, ``$nin``, ``$exists``, ``$regex``, ``$contains``,
    and logical combinators ``$or``, ``$and``, ``$not``.

    Special cases:
    * ``$or: []`` is a wildcard — always matches (no conditions = nothing to reject).
    * ``$contains`` on a non-iterable field returns ``False`` instead of crashing.
    * ``$regex`` escapes the pattern to avoid ``re.error`` on invalid input.

    Args:
        attrs: Node attributes dict.
        query: Query dict with field/value pairs or logical combinators.

    Returns:
        True if all conditions match.
    """
    for key, condition in query.items():
        if key == "$or":
            # Empty $or is a wildcard — no conditions to reject
            if not condition:
                continue
            if not any(_match(attrs, sub) for sub in condition):
                return False
            continue
        if key == "$and":
            if not all(_match(attrs, sub) for sub in condition):
                return False
            continue
        if key == "$not":
            if _match(attrs, condition):
                return False
            continue

        value = attrs.get(key)

        if isinstance(condition, dict):
            for op, expected in condition.items():
                if op not in _OPS:
                    raise ValueError(f"Operador desconegut: {op}")
                if value is None and op != "$exists":
                    return False
                if not _OPS[op](value, expected):
                    return False
        elif isinstance(condition, list):
            # Array condition: field in [val1, val2, ...]
            if value not in condition:
                return False
        else:
            # Short form: {"tipus": "Concepte"} → {"tipus": {"$eq": "Concepte"}}
            if value != condition:
                return False
    return True


def _matches_filter(
    attrs: Dict[str, Any],
    filter_dict: Dict[str, Any],
) -> bool:
    """Check if node attributes match the MongoDB-style filter dict.

    Delegates to :func:`_match` which supports logical combinators
    ``$or``, ``$and``, ``$not`` in addition to the standard operators.

    Args:
        attrs: Node attributes dict.
        filter_dict: Filter dict with field/value pairs.

    Returns:
        True if all conditions match.
    """
    return _match(attrs, filter_dict)


def _apply_projection(
    doc: Dict[str, Any],
    projection: Dict[str, int],
) -> Dict[str, Any]:
    """Apply MongoDB-style projection to a document.

    Args:
        doc: The document dict.
        projection: Dict of fields to include (1) or exclude (0).

    Returns:
        Projected document dict.
    """
    if not projection:
        return doc

    # Determine include/exclude mode
    include = any(v == 1 for v in projection.values())

    if include:
        # Include mode: keep only specified fields
        return {k: doc.get(k) for k in projection if k in doc}
    else:
        # Exclude mode: keep all except specified fields
        return {k: v for k, v in doc.items() if k not in projection}


def _sort_key(
    doc: Dict[str, Any],
    field: str,
) -> Any:
    """Extract a sort key from a document.

    Handles nested fields like "properties.age".

    Args:
        doc: The document dict.
        field: Field name, optionally dotted (e.g. "properties.age").

    Returns:
        The sort key value.
    """
    parts = field.split(".")
    value = doc
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
        if value is None:
            return None
    return value



# ======================================================================
# Cypher parser for NetworkXGraph
# ======================================================================

import re as _re
from typing import List as _List, Dict as _Dict, Any as _Any, Tuple as _Tuple


def _execute_cypher(
    graph: "NetworkXGraph", cypher: str, params: _Dict[str, _Any]
) -> _List[_Dict[str, _Any]]:
    """Execute a simplified Cypher query on a NetworkXGraph."""
    # Normalize whitespace
    cypher = " ".join(cypher.split()).strip()
    # Substitute parameters
    cypher = _substitute_params(cypher, params)
    # Parse into clauses
    clauses = _parse_cypher(cypher)
    # Execute clauses sequentially
    state: _Dict[str, _Any] = {
        "graph": graph,
        "bindings": [{}],
        "next_label": max(graph._node_attrs.keys(), default=0) + 1,
    }
    for clause_type, clause_body in clauses:
        if clause_type == "match":
            state = _exec_match(state, clause_body)
        elif clause_type == "merge":
            state = _exec_merge(state, clause_body)
        elif clause_type == "create":
            state = _exec_create(state, clause_body)
        elif clause_type == "delete":
            state = _exec_delete(state, clause_body)
        elif clause_type == "set":
            state = _exec_set(state, clause_body)
        elif clause_type == "with":
            state = _exec_with(state, clause_body)
        elif clause_type == "return":
            return _exec_return(state, clause_body)
        elif clause_type == "limit":
            state = _exec_limit(state, clause_body)
        elif clause_type == "order_by":
            state = _exec_order_by(state, clause_body)
    return state["bindings"]


def _substitute_params(cypher: str, params: _Dict[str, _Any]) -> str:
    """Replace $param_name with actual values in the Cypher string."""
    def replacer(m: _re.Match) -> str:
        key = m.group(1)
        if key not in params:
            raise ValueError(f"Missing parameter: {key}")
        val = params[key]
        if isinstance(val, str):
            return f'"{val}"'
        if isinstance(val, bool):
            return str(val).lower()
        return str(val)
    return _re.sub(r"\$(\w+)", replacer, cypher)


def _parse_cypher(cypher: str) -> _List[_Tuple[str, str]]:
    """Split a Cypher query into (clause_type, clause_body) pairs."""
    parts = _re.split(r"\s+(?=MATCH\s|MERGE\s|CREATE\s|DELETE\s|DETACH\sDELETE\s|SET\s|WITH\s|RETURN\s|LIMIT\s)", cypher, flags=_re.IGNORECASE)
    result = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        upper = part.upper()
        if upper.startswith("MATCH "):
            result.append(("match", part[6:].strip()))
        elif upper.startswith("MERGE "):
            result.append(("merge", part[6:].strip()))
        elif upper.startswith("CREATE "):
            result.append(("create", part[7:].strip()))
        elif upper.startswith("DETACH DELETE "):
            result.append(("delete", part[14:].strip()))
        elif upper.startswith("DELETE "):
            result.append(("delete", part[7:].strip()))
        elif upper.startswith("SET "):
            result.append(("set", part[4:].strip()))
        elif upper.startswith("WITH "):
            result.append(("with", part[5:].strip()))
        elif upper.startswith("RETURN "):
            result.append(("return", part[7:].strip()))
        elif upper.startswith("LIMIT "):
            result.append(("limit", part[6:].strip()))
        elif upper.startswith("ORDER BY "):
            result.append(("order_by", part[9:].strip()))
    return result


def _resolve_node(
    graph: "NetworkXGraph", node_id: int
) -> _Dict[str, _Any]:
    """Convert a NetworkX node to a dict representation."""
    attrs = graph._node_attrs.get(node_id, {})
    label = attrs.get("main_label", "Node")
    return {"labels": [label], "properties": dict(attrs)}


def _resolve_edge(
    graph: "NetworkXGraph", edge_key: _Tuple[int, int, str]
) -> _Dict[str, _Any]:
    """Convert a NetworkX edge to a dict representation."""
    attrs = graph._edge_attrs.get(edge_key, {})
    return {
        "type": edge_key[2],
        "start_node": edge_key[0],
        "end_node": edge_key[1],
        "properties": dict(attrs),
    }


def _exec_match(
    state: _Dict[str, _Any], body: str
) -> _Dict[str, _Any]:
    """Execute a MATCH clause."""
    bindings = state["bindings"]
    graph = state["graph"]

    # Extract WHERE conditions
    pattern = body.split(" WHERE ")[0] if " WHERE " in body.upper() else body
    where_clause = body.split(" WHERE ")[1] if " WHERE " in body.upper() else ""

    # Parse ORDER BY
    order_match = _re.search(r"\s+ORDER BY\s+", pattern, _re.IGNORECASE)
    if order_match:
        order_clause = pattern[order_match.end() :].strip()
        pattern = pattern[: order_match.start()].strip()
    else:
        order_clause = None

    # Parse LIMIT
    limit_match = _re.search(r"\s+LIMIT\s+(\d+)", pattern, _re.IGNORECASE)
    if limit_match:
        limit_val = int(limit_match.group(1))
        pattern = pattern[: limit_match.start()].strip()
    else:
        limit_val = None

    # Parse the pattern — find all node variables and edge specs
    nodes_found: _Dict[str, _Dict[str, _Any]] = {}
    edge_specs: _List[_Tuple[str, str, str, str]] = []  # (src_var, edge_var, dir, dst_var)

    # Extract node variables: (var:Label {props}) or (var)
    for m in _re.finditer(r"\((\w+)(?::(\w+))?\s*(\{[^}]*\})?\)", pattern):
        var_name = m.group(1)
        label = m.group(2) or None
        props_str = m.group(3) or ""
        props = _parse_props(props_str)
        nodes_found[var_name] = {"label": label, "props": props}

    # Extract edge specs: [var] with direction ->, <-, or -
    for m in _re.finditer(r"-(\[([\w]*)\])?-([>])?-", pattern):
        edge_var = m.group(2) or None
        direction = m.group(3)  # '>' or None
        edge_specs.append((None, edge_var, direction, None))  # placeholders

    # Now map edge variables to the node variables on each side
    # Walk through the pattern to find src/dst nodes for each edge
    # Strategy: split pattern by edge patterns to find connected nodes
    # Find all edge patterns: (src)-[edge]->(dst)
    edge_matches = list(
        _re.finditer(
            r"\((\w+)(?::\w+)?\s*(?:\{[^}]*\})?\)\s*-\[([\w]*)\]\s*->\s*\((\w+)(?::\w+)?\s*(?:\{[^}]*\})?\)",
            pattern,
        )
    )
    edge_matches += list(
        _re.finditer(
            r"\((\w+)(?::\w+)?\s*(?:\{[^}]*\})?\)\s*<-\[([\w]*)\]\s*-\s*\((\w+)(?::\w+)?\s*(?:\{[^}]*\})?\)",
            pattern,
        )
    )
    edge_matches += list(
        _re.finditer(
            r"\((\w+)(?::\w+)?\s*(?:\{[^}]*\})?\)\s*-\[([\w]*)\]\s*-\s*\((\w+)(?::\w+)?\s*(?:\{[^}]*\})?\)",
            pattern,
        )
    )
    # Deduplicate by span
    seen_spans = set()
    unique_edges = []
    for em in edge_matches:
        if em.span() not in seen_spans:
            seen_spans.add(em.span())
            unique_edges.append(em)
    edge_matches = unique_edges

    if edge_matches:
        # Re-parse nodes from the edge matches, extracting labels and props
        nodes_found = {}
        for em in edge_matches:
            src_var = em.group(1)
            src_label = None
            src_props_str = None
            # Extract label and props from src node text
            src_match = _re.match(r'\((\w+)(?::(\w+))?\s*(\{[^}]*\})?\)', em.group(1) + em.group(0)[em.start(1):em.start(2) if em.lastindex >= 2 else len(em.group(0))])
            # Simpler: extract from the matched group text
            src_text = em.group(0)[em.start(1):em.end(1)]
            # Find the full src node pattern
            src_full = _re.search(r'\(\w+(?::\w+)?\s*\{[^}]*\}?\)', em.group(0)[:em.start(3)])
            if src_full:
                nm = _re.match(r'\((\w+)(?::(\w+))?\s*(\{[^}]*\})?\)', src_full.group())
                if nm:
                    src_label = nm.group(2)
                    src_props_str = nm.group(3)
            src_props = _parse_props(src_props_str) if src_props_str else {}
            nodes_found[src_var] = {"label": src_label, "props": src_props}

            dst_var = em.group(3)
            dst_full = _re.search(r'\(\w+(?::\w+)?\s*\{[^}]*\}?\)', em.group(0)[em.start(3):])
            if dst_full:
                nm2 = _re.match(r'\((\w+)(?::(\w+))?\s*(\{[^}]*\})?\)', dst_full.group())
                if nm2:
                    dst_label = nm2.group(2)
                    dst_props_str = nm2.group(3)
            else:
                dst_label = None
                dst_props_str = None
            dst_props = _parse_props(dst_props_str) if dst_props_str else {}
            nodes_found[dst_var] = {"label": dst_label, "props": dst_props}

            dir_char = ">" if "->" in em.group(0) else ("<" if "<-" in em.group(0) else "-")
            edge_specs.append((src_var, em.group(2), dir_char, dst_var))

    # Generate bindings based on pattern
    new_bindings = []
    if not nodes_found:
        # No new nodes found — pass through existing bindings unchanged
        return {"bindings": bindings, "graph": graph, "next_label": state["next_label"]}

    # Handle edge patterns
    if edge_specs:
        for src_var, edge_var, direction, dst_var in edge_specs:
            for src_id, src_attrs in graph._node_attrs.items():
                src_label = src_attrs.get("main_label", "Node")
                src_spec = nodes_found.get(src_var, {"label": None, "props": {}})
                if src_spec["label"] and src_label != src_spec["label"]:
                    continue
                if src_spec["props"] and not all(
                    src_attrs.get(k) == v for k, v in src_spec["props"].items()
                ):
                    continue

                for dst_id, dst_attrs in graph._node_attrs.items():
                    dst_label = dst_attrs.get("main_label", "Node")
                    dst_spec = nodes_found.get(dst_var, {"label": None, "props": {}})
                    if dst_spec["label"] and dst_label != dst_spec["label"]:
                        continue
                    if dst_spec["props"] and not all(
                        dst_attrs.get(k) == v for k, v in dst_spec["props"].items()
                    ):
                        continue

                    # Check for edge based on direction
                    edge_found = False
                    edge_key_result = None
                    if direction == ">" or direction == "-":
                        for ek in graph._graph.edges(keys=True):
                            if ek[0] == src_id and ek[1] == dst_id:
                                edge_found = True
                                edge_key_result = ek
                                break
                    if direction == "<" or direction == "-":
                        for ek in graph._graph.edges(keys=True):
                            if ek[0] == dst_id and ek[1] == src_id:
                                edge_found = True
                                edge_key_result = ek
                                break

                    if edge_found:
                        row = {
                            src_var: _resolve_node(graph, src_id),
                            dst_var: _resolve_node(graph, dst_id),
                        }
                        if edge_var:
                            row[edge_var] = _resolve_edge(graph, edge_key_result)
                        new_bindings.append(row)

    # Handle single node pattern (no edges)
    elif len(nodes_found) == 1:
        var_name, node_spec = list(nodes_found.items())[0]
        label_filter = node_spec["label"]
        prop_filter = node_spec["props"]

        for nid, attrs in graph._node_attrs.items():
            node_label = attrs.get("main_label", "Node")
            if label_filter and node_label != label_filter:
                continue
            if prop_filter and not all(
                attrs.get(k) == v for k, v in prop_filter.items()
            ):
                continue
            row = {var_name: _resolve_node(graph, nid)}
            new_bindings.append(row)

    # Merge with existing bindings from previous MATCH clauses
    merged: _List[_Dict[str, _Any]] = []
    for existing in bindings:
        for row in new_bindings:
            merged.append({**existing, **row})
    if not merged:
        merged = new_bindings  # No previous bindings — use new ones

    # Apply WHERE clause
    if where_clause:
        merged = _apply_where(merged, where_clause, graph)

    return {
        "bindings": merged,
        "graph": graph,
        "next_label": state["next_label"],
    }


def _exec_merge(
    state: _Dict[str, _Any], body: str
) -> _Dict[str, _Any]:
    """Execute a MERGE clause (MATCH + CREATE if not found).

    MERGE tries to find a matching node/edge; if not found, creates it.
    Supports patterns like:
      (n:Label {prop: val})                          — node merge
      (a)-[r:REL_TYPE]->(b)                          — edge merge (uses bindings)
      (a:LabelA {props})-[r:REL_TYPE]->(b:LabelB {props})  — full pattern
    """
    graph = state["graph"]
    next_label = state["next_label"]
    bindings = state["bindings"]

    # Extract WHERE clause
    where_clause = ""
    if " WHERE " in body.upper():
        idx = body.upper().index(" WHERE ")
        where_clause = body[idx + 6:].strip()
        pattern = body[:idx].strip()
    else:
        pattern = body.strip()

    # Detect edge pattern: (src)-[rel]->(dst) or (src)-[rel]-(dst)
    # Format: (var:Label {props})-[var:TYPE]->(var:Label {props})
    edge_spec_pattern = _re.compile(
        r"\((\w+)(?::(\w+))?\s*(\{[^}]*\})?\)"
        r"\s*-\s*\[([\w]*)\s*(?::(\w+))?\]\s*-*\s*->?\s*"
        r"\((\w+)(?::(\w+))?\s*(\{[^}]*\})?\)"
    )
    edge_matches = list(edge_spec_pattern.finditer(pattern))

    # Parse standalone node patterns: (var:Label {props})
    node_pattern = _re.compile(r"\((\w+)(?::(\w+))?\s*(\{[^}]*\})?\)")
    node_matches = list(node_pattern.finditer(pattern))

    # Build a map of node variable -> spec
    nodes_found: _Dict[str, _Dict[str, _Any]] = {}
    for m in node_matches:
        var_name = m.group(1)
        label = m.group(2) or "Node"
        props_str = m.group(3) or ""
        props = _parse_props(props_str)
        nodes_found[var_name] = {"label": label, "props": props}

    # Track variable -> node dict mapping
    var_node_map: _Dict[str, _Dict[str, _Any]] = {}

    if edge_matches:
        # ── Edge MERGE: (a)-[r:REL]->(b) ──────────────────────────
        # Strategy: resolve src/dst from existing bindings (MATCH),
        # then create the edge if it doesn't exist.
        # If bindings don't have the vars, fall back to node lookup.

        for em in edge_matches:
            src_var = em.group(1)
            src_label = em.group(2) or "Node"
            src_props_str = em.group(3) or ""
            edge_var = em.group(4) or None
            rel_type = em.group(5) or "MERGE"
            dst_var = em.group(6)
            dst_label = em.group(7) or "Node"
            dst_props_str = em.group(8) or ""

            # Resolve src node
            src_id = None
            if src_var in bindings[0] and bindings[0][src_var]:
                # Try to find node matching the binding
                src_node = bindings[0][src_var]
                for nid, attrs in graph._node_attrs.items():
                    if src_node["properties"] == attrs:
                        src_id = nid
                        break
            if src_id is None:
                # Try to find by label + props from pattern
                src_props = _parse_props(src_props_str)
                match_props = {k: v for k, v in src_props.items() if k != "main_label"}
                for nid, attrs in graph._node_attrs.items():
                    if attrs.get("main_label") == src_label and (
                        not match_props or all(
                            attrs.get(k) == v for k, v in match_props.items()
                        )
                    ):
                        src_id = nid
                        break

            # Resolve dst node
            dst_id = None
            if dst_var in bindings[0] and bindings[0][dst_var]:
                dst_node = bindings[0][dst_var]
                for nid, attrs in graph._node_attrs.items():
                    if dst_node["properties"] == attrs:
                        dst_id = nid
                        break
            if dst_id is None:
                dst_props = _parse_props(dst_props_str)
                match_props = {k: v for k, v in dst_props.items() if k != "main_label"}
                for nid, attrs in graph._node_attrs.items():
                    if attrs.get("main_label") == dst_label and (
                        not match_props or all(
                            attrs.get(k) == v for k, v in match_props.items()
                        )
                    ):
                        dst_id = nid
                        break

            if src_id is not None and dst_id is not None:
                # Check if edge already exists (directional only: ->)
                has_edge = graph._graph.has_edge(src_id, dst_id, key=rel_type)

                if not has_edge:
                    graph._graph.add_edge(src_id, dst_id, key=rel_type, rel_type=rel_type)
                    graph._edge_attrs[(src_id, dst_id, rel_type)] = {}

                # Update bindings
                if src_id is not None:
                    var_node_map[src_var] = _resolve_node(graph, src_id)
                if dst_id is not None:
                    var_node_map[dst_var] = _resolve_node(graph, dst_id)

            # If src or dst not found, skip (edge not created)
            # This is the same behavior as Neo4j (MERGE with unmatched nodes fails silently)

    else:
        # ── Node MERGE: (n:Label {props}) ─────────────────────────
        # Try to find existing node or create new one.

        for var_name, spec in nodes_found.items():
            label = spec["label"]
            props = spec["props"]
            match_props = {k: v for k, v in props.items() if k != "main_label"}

            # Try to find an existing node matching label + props
            found_id = None
            for nid, attrs in graph._node_attrs.items():
                node_label = attrs.get("main_label", "Node")
                if node_label != label:
                    continue
                if match_props and not all(
                    attrs.get(k) == v for k, v in match_props.items()
                ):
                    continue
                found_id = nid
                break

            if found_id is not None:
                # Node exists — use it
                var_node_map[var_name] = _resolve_node(graph, found_id)
            else:
                # Node does not exist — create it
                node_id = next_label
                next_label += 1
                node_data = dict(props)
                node_data["main_label"] = label
                graph._node_attrs[node_id] = node_data
                graph._graph.add_node(node_id, main_label=label)
                var_node_map[var_name] = _resolve_node(graph, node_id)

    # Update bindings with merged nodes/edges
    for binding in bindings:
        for var_name, node_dict in var_node_map.items():
            binding[var_name] = node_dict

    return {
        "bindings": bindings,
        "graph": graph,
        "next_label": next_label,
    }


def _exec_create(
    state: _Dict[str, _Any], body: str
) -> _Dict[str, _Any]:
    """Execute a CREATE clause."""
    graph = state["graph"]
    next_label = state["next_label"]

    # Parse: (n:Label {prop: val, ...})
    match = _re.match(r"\((\w+)(?::(\w+))?\s*\{([^}]*)\}\)", body)
    if not match:
        return state

    var_name = match.group(1)
    label = match.group(2) or "Node"
    props_str = match.group(3)
    props = _parse_props(props_str)

    # Add main_label to properties
    props["main_label"] = label

    # Create node
    node_id = next_label
    next_label += 1
    graph._node_attrs[node_id] = dict(props)
    graph._graph.add_node(node_id, main_label=label)

    # Update binding
    for binding in state["bindings"]:
        binding[var_name] = _resolve_node(graph, node_id)

    return {"bindings": state["bindings"], "graph": graph, "next_label": next_label}


def _exec_delete(
    state: _Dict[str, _Any], body: str
) -> _Dict[str, _Any]:
    """Execute a DELETE clause."""
    graph = state["graph"]
    vars_to_delete = [v.strip() for v in body.split(",")]

    for binding in state["bindings"]:
        for var in vars_to_delete:
            if var in binding:
                node = binding[var]
                for nid, attrs in graph._node_attrs.items():
                    if node["properties"] == attrs:
                        graph._graph.remove_node(nid)
                        del graph._node_attrs[nid]
                        break

    return state


def _exec_set(
    state: _Dict[str, _Any], body: str
) -> _Dict[str, _Any]:
    """Execute a SET clause."""
    graph = state["graph"]
    # Split into individual assignments.
    # Cypher supports both comma-separated and space-separated:
    #   SET n.`a` = "1", n.`b` = "2"
    #   SET n.`a` = "1" n.`b` = "2"
    raw_parts = _re.split(r",\s*", body.strip())
    assignments: _List[_Tuple[str, str, str]] = []
    for part in raw_parts:
        part = part.strip()
        if not part:
            continue
        # Split space-separated assignments
        for m in _re.finditer(
            r"(\w+)\.(?:`(\w+)`|(\w+))\s*=\s*(.+?)(?=\s+\w+\.(?:`|\w)|$)",
            part,
        ):
            var = m.group(1)
            prop = m.group(2) or m.group(3)
            val_str = m.group(4).strip()
            assignments.append((var, prop, val_str))

    # Resolve each variable to a node ID once, then apply all assignments
    var_to_id: _Dict[str, int] = {}
    for binding in state["bindings"]:
        if not binding:
            continue
        for var, prop, val_str in assignments:
            if var in var_to_id:
                continue  # already resolved
            if var not in binding:
                continue
            node = binding[var]
            for nid, attrs in graph._node_attrs.items():
                if node["properties"] == attrs:
                    var_to_id[var] = nid
                    break
            if var in var_to_id:
                break  # all vars resolved from this binding

    # Apply all assignments to resolved node IDs
    for var, prop, val_str in assignments:
        if var not in var_to_id:
            continue
        nid = var_to_id[var]
        val_str = val_str.strip()
        if val_str.startswith('"') and val_str.endswith('"'):
            val = val_str[1:-1]
        elif val_str.startswith("'") and val_str.endswith("'"):
            val = val_str[1:-1]
        else:
            try:
                val = int(val_str)
            except ValueError:
                try:
                    val = float(val_str)
                except ValueError:
                    val = val_str
        graph._node_attrs[nid][prop] = val
        graph._graph.nodes[nid][prop] = val

    return state


def _exec_with(
    state: _Dict[str, _Any], body: str
) -> _Dict[str, _Any]:
    """Execute a WITH clause (pass-through for now)."""
    return state


def _exec_limit(
    state: _Dict[str, _Any], limit_str: str
) -> _Dict[str, _Any]:
    """Execute a LIMIT clause."""
    limit_val = int(limit_str)
    state["bindings"] = state["bindings"][:limit_val]
    return state


def _exec_order_by(
    state: _Dict[str, _Any], order_str: str
) -> _Dict[str, _Any]:
    """Execute an ORDER BY clause."""
    desc = " desc" in order_str.lower()
    match = _re.match(r"(\w+)\.(\w+)", order_str)
    if match:
        var, prop = match.group(1), match.group(2)
        state["bindings"] = sorted(
            state["bindings"],
            key=lambda b: b.get(var, {}).get("properties", {}).get(prop, 0)
            if var in b
            else 0,
            reverse=desc,
        )
    return state


def _exec_return(
    state: _Dict[str, _Any], body: str
) -> _List[_Dict[str, _Any]]:
    """Execute a RETURN clause and produce final results."""
    bindings = state["bindings"]

    # Check for count(*) or count(n)
    count_match = _re.search(r"count\((\w+|\*)\)\s+AS\s+(\w+)", body, _re.IGNORECASE)
    if count_match:
        as_var = count_match.group(2)
        return [{as_var: len(bindings)}]

    # Check for sum, avg, min, max
    agg_match = _re.search(r"(sum|avg|min|max)\((\w+)\)\s+AS\s+(\w+)", body, _re.IGNORECASE)
    if agg_match:
        agg_func = agg_match.group(1).lower()
        agg_var = agg_match.group(2)
        as_var = agg_match.group(3)
        values = []
        for binding in bindings:
            if agg_var in binding:
                node = binding[agg_var]
                if isinstance(node, dict) and "properties" in node:
                    for v in node["properties"].values():
                        if isinstance(v, (int, float)):
                            values.append(v)
        if values:
            if agg_func == "sum":
                result = sum(values)
            elif agg_func == "avg":
                result = sum(values) / len(values)
            elif agg_func == "min":
                result = min(values)
            elif agg_func == "max":
                result = max(values)
        else:
            result = 0
        return [{as_var: result}]

    # Parse return variables
    items = _re.split(r",\s*(?![^(]*\))", body)
    parsed_items = []
    for item in items:
        item = item.strip()
        alias_match = _re.match(r"(.+?)\s+AS\s+(\w+)", item)
        if alias_match:
            expr = alias_match.group(1).strip()
            alias = alias_match.group(2)
        else:
            expr = item
            alias = None
        parsed_items.append((expr, alias))

    # Build one result row per binding
    results = []
    for binding in bindings:
        row: _Dict[str, _Any] = {}
        for expr, alias in parsed_items:
            if expr == "*":
                row.update(binding)
            elif _re.match(r"(\w+)\((\w+)\)", expr):
                # Handle function calls like type(r), count(n)
                func_match = _re.match(r"(\w+)\((\w+)\)", expr)
                func_name, func_var = func_match.groups()
                if func_name == "type" and func_var in binding:
                    val = binding[func_var]
                    if isinstance(val, dict) and "type" in val:
                        row[alias or expr] = val["type"]
            elif "." in expr:
                var, prop = expr.split(".", 1)
                if var in binding:
                    node = binding[var]
                    if isinstance(node, dict) and "properties" in node:
                        val = node["properties"].get(prop)
                        row[alias or f"{var}.{prop}"] = val
            else:
                if expr in binding:
                    row[alias or expr] = binding[expr]
        results.append(row)

    return results


def _parse_props(props_str: str) -> _Dict[str, _Any]:
    """Parse a property string like 'prop1: val1, prop2: val2'.

    Accepts both bare strings (``'prop1: val1'``) and brace-enclosed
    strings (``'{prop1: val1}'``) — the leading/trailing braces are
    stripped before splitting.
    """
    if not props_str:
        return {}
    # Strip surrounding braces if present (e.g. '{name: "Alice"}' → 'name: "Alice"')
    props_str = props_str.strip().strip("{}").strip()
    if not props_str:
        return {}
    props = {}
    for pair in props_str.split(","):
        pair = pair.strip()
        if ":" in pair:
            key, val = pair.split(":", 1)
            key = key.strip()
            val = val.strip()
            if val.startswith('"') and val.endswith('"'):
                props[key] = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                props[key] = val[1:-1]
            else:
                try:
                    props[key] = int(val)
                except ValueError:
                    try:
                        props[key] = float(val)
                    except ValueError:
                        props[key] = val
    return props


def _apply_where(
    bindings: _List[_Dict[str, _Any]],
    where_clause: str,
    graph: "NetworkXGraph",
) -> _List[_Dict[str, _Any]]:
    """Apply a WHERE clause to bindings."""
    conditions = _re.split(r"\s+AND\s+", where_clause, flags=_re.IGNORECASE)
    filtered = []
    for binding in bindings:
        match_all = True
        for cond in conditions:
            cond = cond.strip()
            eq_match = _re.match(r"(\w+)\.(\w+)\s*=\s*(.+)", cond)
            if eq_match:
                var, prop, val_str = eq_match.group(1), eq_match.group(2), eq_match.group(3).strip()
                if var in binding:
                    node = binding[var]
                    if isinstance(node, dict) and "properties" in node:
                        actual = node["properties"].get(prop)
                        if val_str.startswith('"') and val_str.endswith('"'):
                            expected = val_str[1:-1]
                        else:
                            try:
                                expected = int(val_str)
                            except ValueError:
                                expected = val_str
                        if actual != expected:
                            match_all = False
                            break
        if match_all:
            filtered.append(binding)
    return filtered
