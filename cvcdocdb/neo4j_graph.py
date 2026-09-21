"""Neo4jGraph — Full Neo4j driver integration layer for the DRM graph model.

Provides graph operations (insert, update, delete nodes and relations)
with FK validation, cascade delete, and WeakNode parent propagation.
"""

from neo4j import GraphDatabase
from neo4j.exceptions import ConstraintError, TransactionError
from . import Node, Relation, WeakRelation
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from tqdm import tqdm
import re
import threading
import warnings

# Cypher does not support parameterizing labels or relationship types, so
# they are interpolated directly into query strings. Restrict them (and any
# property key used to build a WHERE/MERGE clause) to safe identifiers to
# prevent Cypher injection via a crafted main_label / relation type / pk key.
_CYPHER_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_cypher_identifier(name: Any, kind: str) -> str:
    """Ensure a value used as a Cypher label/relation-type/property key is safe.

    Raises:
        ValueError: If `name` is not a valid, safe identifier.
    """
    if not isinstance(name, str) or not _CYPHER_IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid {kind} {name!r}: must match {_CYPHER_IDENTIFIER_RE.pattern!r} "
            "to be used safely in a Cypher query."
        )
    return name


def _escape_cypher_string(value: str) -> str:
    """Escape a string value for safe interpolation inside a double-quoted
    Cypher string literal (backslashes and double quotes)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


class Neo4jGraph:
    """Neo4j-backed graph store for the DRM document representation model.

    This class wraps the Neo4j Python driver and provides high-level
    operations for managing graph nodes and relations with foreign key
    validation, cascade delete strategies (CASCADE, RESTRICT, SET NULL),
    and WeakNode parent-child propagation.

    Example:
        >>> graph = Neo4jGraph(
        ...     url="bolt://localhost:7687",
        ...     user="neo4j",
        ...     password="secret",
        ...     database="mydb",
        ... )
        >>> doc = Node(pk={"doc": "DOC-001"}, main_label="Document")
        >>> graph.insertNode(doc, replace=True)
        >>> graph.close()

    Args:
        url: Neo4j connection URL (e.g. ``bolt://localhost:7687``).
        user: Authentication username.
        password: Authentication password.
        database: Target database name. Defaults to the Neo4j default.
    """

    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        database: Optional[str] = None,
    ) -> None:
        self._driver = GraphDatabase.driver(url, auth=(user, password))
        self._tx = None
        self._closed = False
        # Internal tracking: neo4j_id -> pk dict (for get_node_pks)
        self._node_pks: Dict[int, Dict[str, Any]] = {}
        if database is None:
            self._session = self._driver.session()
        else:
            self._session = self._driver.session(database=database)
        # self._version = list(list(self._session._pool.connections.values())[0])[
        #    0
        # ].PROTOCOL_VERSION[0]
        self._version = self._driver.get_server_info().protocol_version[0]
        # FK index: neo4j_id → set of (other_id, rel_type, direction)
        # direction: "out" = edge starts here, "in" = edge ends here
        self._fk_index: Dict[int, Set[Tuple[int, str, str]]] = {}
        # Propagation initialization tracking
        self._propagation_initialized: bool = False
        self._propagation_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Neo4j driver 5.x+ uses execute_write/execute_read instead of
    # write_transaction/read_transaction.  Compat shim for both.
    # ------------------------------------------------------------------

    @staticmethod
    def _write_transaction(session, func, *args, **kwargs):
        """Run *func* in a write transaction (compat shim)."""
        if hasattr(session, "execute_write"):
            return session.execute_write(func, *args, **kwargs)
        return session.write_transaction(func, *args, **kwargs)

    @staticmethod
    def _read_transaction(session, func, *args, **kwargs):
        """Run *func* in a read transaction (compat shim)."""
        if hasattr(session, "execute_read"):
            return session.execute_read(func, *args, **kwargs)
        return session.read_transaction(func, *args, **kwargs)

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
        """Insert a node into the Neo4j database.

        For WeakNode instances, the parent node is inserted first if
        ``insert_parent=True``.  String dependencies (``dependencies``
        attribute) are automatically materialised as ``Valor`` nodes
        connected by typed edges.

        Args:
            node: The node to insert.
            insert_parent: If the node is a WeakNode, insert its parent
                first. Defaults to True.
            update: If True, MERGE the node and update attributes without
                deleting it. Use when adding new attributes to an existing
                node. Defaults to False.
            replace: If True and the node already exists, delete it
                (with detach) and create a fresh one. The caller must
                recreate relations. Defaults to False.

        Returns:
            The Neo4j internal node id of the inserted node.
        """

        if node.main_label:
            _validate_cypher_identifier(node.main_label, "main_label")

        has_parent = insert_parent if node["is_weak"] else False
        has_dependencies = True if node["dependencies"] else False

        # Allow recursion on parent node insertion but without replacing "parent nodes" if already exists
        inici = False

        # self._session.write_transaction(self._create_constraint, node.main_label, list(node['pk']['pk'].keys()))
        if self._tx is None:
            self._tx = self._session.begin_transaction()
            inici = True

        try:
            if has_parent:
                self.insertNode(
                    node["parent"],
                    insert_parent=insert_parent,
                    update=True,
                    replace=False,
                )

            id = self._insertNode(node, update=update, replace=replace)

            if has_dependencies:
                deps = node["dependencies"]
                for k in deps.keys():
                    v = deps[k]
                    id_v = self._insertNode(v,update=True, replace=False)
                    rel = Relation(node, deps[k], k.upper())
                    self.insertRelation(rel, update=True)
                    # print(k,deps[k])

            if inici:
                # print(node)
                self._tx.commit()
        except ConstraintError as err:
            try:
                self._tx.rollback()
            except Exception:
                pass
            try:
                self._tx.close()
            except Exception:
                pass
            self._tx = None
            raise RuntimeError("Duplicate key: " + err.message) from err
        except TransactionError as err:
            msg = str(err)
            if "Duplicate key" in msg or "NODE KEY" in msg or "Transaction failed" in msg or "ConstraintValidationFailed" in msg:
                try:
                    self._tx.rollback()
                except Exception:
                    pass
                try:
                    self._tx.close()
                except Exception:
                    pass
                self._tx = None
                raise RuntimeError("Duplicate key: " + msg) from err
            print(err)
            try:
                self._tx.rollback()
            except Exception:
                pass
            try:
                self._tx.close()
            except Exception:
                pass
            self._tx = None
            raise
        finally:
            if inici and self._tx is not None:
                try:
                    self._tx.close()
                except Exception:
                    pass
                self._tx = None
        return id  # only reached if no exception was raised above

    def deleteNode(
        self,
        node: Node,
        propagation: bool = False,
        detach: bool = False,
        on_delete: str = "cascade",
    ) -> bool:
        """Delete a node and optionally its connected subgraph.

        Handles ON DELETE strategies, WeakNode cascade propagation,
        and FK index cleanup.

        Args:
            node: The node to delete.
            propagation: If True, recursively delete child nodes linked
                via edges with ``_propagate=TRUE`` (used by WeakNode).
            detach: If True, use Neo4j ``DETACH DELETE`` to remove
                the node and all connected edges.
            on_delete: Deletion strategy — ``"cascade"`` (default),
                ``"restrict"``, or ``"set_null"``.

        Returns:
            True if the node was deleted, False if deletion was refused
            (e.g. RESTRICT with existing edges) or the node was not found.
        """
        if node.main_label:
            _validate_cypher_identifier(node.main_label, "main_label")

        node.version = self._version

        inici = False

        if self._tx is None:
            self._tx = self._session.begin_transaction()
            inici = True

        try:
            if propagation:
                # Get the list of nodes to be deleted before
                nodeList = self._get_propagated_nodes(self._tx, node)
                for child in nodeList:
                    self.deleteNode(
                        Node(
                            neo4j_id=child[0]._id,
                            alternative_labels=list(child[0]._labels),
                            **child[0]._properties,
                        ),
                        propagation=propagation,
                        detach=True,
                    )
                # After deleting children, remove any remaining outgoing edges
                if node.neo4j_id is not None:
                    self._tx.run(
                        "MATCH (a)-[r]->(b) WHERE id(a)="
                        + str(node.neo4j_id) + " DELETE r"
                    )

            if on_delete == "set_null":
                # ON DELETE SET NULL: delete node, remove edges, keep neighbors
                if node.neo4j_id is not None:
                    self._remove_from_fk_index(node.neo4j_id)
                    # Remove all edges before the node (Neo4j won't allow deleting
                    # a node that still has relationships with a plain DELETE)
                    self._tx.run(
                        "MATCH (a) WHERE id(a) = "
                        + str(node.neo4j_id)
                        + " DETACH DELETE a"
                    )
                    res = True
                else:
                    res = False
            elif detach:
                # ON DELETE CASCADE: recursively delete connected nodes first
                if node.neo4j_id is not None:
                    self._cascade_delete(self._tx, node)
                    self._remove_from_fk_index(node.neo4j_id)
                res = self._delete_node(self._tx, node, detach=detach)
            else:
                # ON DELETE RESTRICT: check if node has children (WeakNode) before deleting
                # propagation=False means we refuse to delete if the node has children
                if propagation is False and node.neo4j_id is not None:
                    children = self._get_propagated_nodes(self._tx, node)
                    if children:
                        raise RuntimeError(
                            "ON DELETE RESTRICT: node has child WeakNode(s) — "
                            "use propagation=True or detach=True to delete."
                        )
                # Also check for any edges (FK violations) — refuse to delete
                if not self._has_no_edges(self._tx, node):
                    raise RuntimeError(
                        "ON DELETE RESTRICT: node has connected edges — "
                        "use detach=True or delete edges first."
                    )
                # Clean FK index before regular delete
                if node.neo4j_id is not None:
                    self._remove_from_fk_index(node.neo4j_id)
                res = self._delete_node(self._tx, node, detach=False)
        except ConstraintError as err:
            print(f"[CVCDocDB Message]: {err.message}")
            return False
        except TransactionError as err:
            msg = str(err)
            if "Duplicate key" in msg or "NODE KEY" in msg or "Transaction failed" in msg or "ConstraintValidationFailed" in msg:
                try:
                    self._tx.rollback()
                except Exception:
                    pass
                try:
                    self._tx.close()
                except Exception:
                    pass
                raise RuntimeError("Duplicate key: " + msg) from err
            print(err)
            self._tx.rollback()
            self._tx.close()
            raise
        else:
            if inici:
                self._tx.commit()
                self._tx.close()
                self._tx = None
            return res
        finally:
            # Ensure transaction is closed even on early return (e.g. RESTRICT).
            # Only close if we opened it (inici=True); recursive calls must not
            # close the parent transaction.
            if inici and self._tx is not None:
                if not self._tx.closed():
                    self._tx.rollback()
                self._tx.close()
                self._tx = None

    def checkNode(self, node: Node, **kwargs: Any) -> Optional[int]:
        """Check if a node exists in the database.

        Looks up the node by its ``neo4j_id`` if set, otherwise searches
        by ``main_label`` and primary key.

        Args:
            node: The node to look up.

        Returns:
            The Neo4j internal node id if found, None otherwise.
        """
        inici = False
        if node is not None:
            # If neo4j_id is already known, return it directly
            if node.neo4j_id is not None:
                return node.neo4j_id
            if node.main_label:
                _validate_cypher_identifier(node.main_label, "main_label")
            if self._tx is None:
                self._tx = self._session.begin_transaction()
                inici = True
            try:
                # return self._session.read_transaction(self._check_node, node['main_label'], node['pk_attributes'])
                res = self._check_node(
                    self._tx, node["main_label"], node["pk_attributes"]
                )
            except TransactionError as err:
                print(err)
                self._tx.rollback()
                self._tx.close()
                self._tx = None
                raise
            else:
                if inici:
                    self._tx.commit()
                    self._tx.close()
                    self._tx = None

                return res
            finally:
                if inici and self._tx is not None:
                    if not self._tx.closed():
                        self._tx.rollback()
                    self._tx.close()
                    self._tx = None
        else:
            return False

    def insertRelation(
        self,
        rel: Relation,
        update: bool = False,
        replace: bool = False,
        **kwargs: Any,
    ) -> int:
        """Insert a directed relation (edge) between two nodes.

        Validates that both source and destination nodes exist before
        creating the edge (FK constraint).

        Args:
            rel: The relation to insert, containing source node,
                destination node, and relation type.
            update: If True, MERGE the relation and update attributes
                without deleting it. Defaults to False.
            replace: If True and the relation already exists, delete it
                and create a fresh one. Defaults to False.

        Returns:
            The Neo4j internal relation id of the inserted relation.

        Raises:
            RuntimeError: If the source or destination node does not exist
                in the database (FK violation).
        """
        _validate_cypher_identifier(rel["type"], "relation type")
        src_label = rel["src"].get("main_label")
        dst_label = rel["dst"].get("main_label")
        if src_label:
            _validate_cypher_identifier(src_label, "main_label")
        if dst_label:
            _validate_cypher_identifier(dst_label, "main_label")

        inici = False

        if self._tx is None:
            self._tx = self._session.begin_transaction()
            inici = True

        try:
            # FK validation: both endpoints must exist before creating the edge
            src_exists = self._validate_fk(self._tx, rel["src"], "src")
            dst_exists = self._validate_fk(self._tx, rel["dst"], "dst")
            if not src_exists:
                raise RuntimeError(
                    f"FK violation: src node with pk={rel['src'].get('pk')} "
                    f"and main_label={rel['src'].get('main_label')} does not exist."
                )
            if not dst_exists:
                raise RuntimeError(
                    f"FK violation: dst node with pk={rel['dst'].get('pk')} "
                    f"and main_label={rel['dst'].get('main_label')} does not exist."
                )
            if update:
                # return self._session.write_transaction(self._update_relation,rel )
                id = self._update_relation(self._tx, rel)
                # Update FK index after upsert
                self._update_fk_index_for_relation(self._tx, rel)
            else:
                # id = self._session.read_transaction(self._check_relation, rel['src'],rel['dst'],rel['type'])
                id = self._check_relation(self._tx, rel["src"], rel["dst"], rel["type"])
                if isinstance(id, int) and not isinstance(id, bool):
                    if replace:
                        # Remove old FK index entries before deleting
                        self._remove_from_fk_index_for_relation(self._tx, rel)
                        # self._session.write_transaction(self._delete_relation,rel)
                        self._delete_relation(self._tx, rel)
                # return self._session.write_transaction(self._create_relation,rel )
                id = self._create_relation(self._tx, rel)
                # Add FK index entries after create
                self._update_fk_index_for_relation(self._tx, rel)
        except ConstraintError as err:
            print("[CVCDocDB Message]: " + err.message)
            raise
        except TransactionError as err:
            print(err)
            self._tx.rollback()
            self._tx.close()
            self._tx = None
            raise
        else:
            if inici:
                self._tx.commit()
                self._tx.close()
                self._tx = None

            return id
        finally:
            if inici and self._tx is not None:
                if not self._tx.closed():
                    self._tx.rollback()
                self._tx.close()
                self._tx = None

    def create(
        self,
        migration: Tuple[List[Node], List[Relation]],
        update: bool = False,
        replace: bool = False,
    ) -> None:
        """Bulk import nodes and relations from a migration plan.

        Iterates over the node and relation lists, inserting each with
        a progress bar.  Uses ``insertNode`` and ``insertRelation``
        internally.

        Args:
            migration: A tuple ``(node_list, relation_list)`` where each
                list contains ``Node`` or ``Relation`` instances.
            update: Passed to ``insertNode`` / ``insertRelation``.
            replace: Passed to ``insertNode`` / ``insertRelation``.
        """
        NodeInformation, RelationInformation = migration
        if len(NodeInformation) > 0:
            for node in tqdm(NodeInformation, desc="Importing nodes"):
                id = self.insertNode(node, update=update, replace=replace)

        if len(RelationInformation) > 0:
            for relation in tqdm(RelationInformation, desc="Creating node links"):
                self.insertRelation(relation, update=update, replace=replace)

    # ------------------------------------------------------------------
    # Public API: query operations
    # ------------------------------------------------------------------

    def get_node_ids(self) -> List[int]:
        """Return all internal node ids in the graph."""
        if self._closed:
            return []
        result = self._session.run("MATCH (n) RETURN id(n) AS nid")
        return [record["nid"] for record in result]

    def get_nodes(self) -> List[int]:
        """Alias for :meth:`get_node_ids` for backward compatibility."""
        return self.get_node_ids()

    def get_node_pks(self) -> List[Dict[str, Any]]:
        """Return all primary keys of nodes in the graph."""
        result = []
        for nid, pk in self._node_pks.items():
            r = self._session.run(
                "MATCH (n) WHERE id(n) = $nid RETURN labels(n)[0] AS label",
                nid=nid
            ).single()
            label = r["label"] if r else ""
            result.append({"main_label": label, "pk": pk})
        return result

    def get_edges(self) -> List[Tuple[int, int, str]]:
        """Return all edges as ``(src_id, dst_id, rel_type)`` tuples."""
        result = self._session.run(
            "MATCH (a)-[r]->(b) RETURN id(a) AS src, id(b) AS dst, type(r) AS rel_type"
        )
        return [
            (record["src"], record["dst"], record["rel_type"])
            for record in result
        ]

    def get_edge_attrs(self, u: int, v: int, key: str) -> Optional[Dict[str, Any]]:
        """Return attributes stored for an edge."""
        _validate_cypher_identifier(key, "relation type")
        result = self._session.run(
            "MATCH (a)-[r:" + key + "]->(b) "
            "WHERE id(a) = $src AND id(b) = $dst "
            "RETURN r",
            src=u, dst=v
        )
        single = result.single()
        if single is None:
            return None
        return dict(single["r"])

    def get_dependency_value(self, node_id: int, relation_type: str) -> Optional[str]:
        """Return the ``name`` property of the node reachable via a single
        outgoing *relation_type* edge from *node_id*.

        Targeted single-hop Cypher query — never scans the whole graph.
        """
        _validate_cypher_identifier(relation_type, "relation type")
        result = self._session.run(
            "MATCH (a)-[:" + relation_type + "]->(b) "
            "WHERE id(a) = $nid "
            "RETURN b.name AS name LIMIT 1",
            nid=node_id,
        )
        single = result.single()
        return single["name"] if single is not None else None

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
        """Execute a raw Cypher query and return results as a list of dicts.

        **Hybrid API** — the first argument accepts either:

        * **str** — a Cypher query string (``MATCH``, ``CREATE``,
          ``DELETE``, ``SET``, ``RETURN``, ``ORDER BY``, ``LIMIT``,
          aggregations, parameter substitution via ``$name``).
        * **dict** — not supported by Neo4jGraph (raises
          ``ValueError``); use a Cypher string instead.

        Each record is a dict mapping return aliases to values.  Node values
        are dicts with ``labels`` and ``properties`` keys.  Relation values
        are dicts with ``type``, ``start_node``, ``end_node``, and
        ``properties`` keys.  Primitive values are returned as-is.

        Args:
            filter_dict: Cypher query string for Neo4jGraph.
                MongoDB-style dicts are not supported — use Cypher instead.
            projection: Ignored for Cypher queries (use ``RETURN`` aliases).
            sort: Ignored for Cypher queries (use ``ORDER BY``).
            limit_val: Ignored for Cypher queries (use ``LIMIT``).
            params: Optional parameter dict for ``$name`` substitution.

        Returns:
            A list of dicts, one per result record.

        Example:
            >>> graph.query("MATCH (n) RETURN n LIMIT 5")
            >>> graph.query(
            ...     "MATCH (n:Person {name: $name}) RETURN n",
            ...     params={"name": "Alice"},
            ... )
        """
        if self._closed:
            raise RuntimeError("Cannot query a closed Neo4jGraph.")
        if isinstance(filter_dict, dict):
            raise ValueError(
                "Neo4jGraph.query() does not support MongoDB-style dict filters. "
                "Use a Cypher query string instead."
            )
        result = self._session.run(
            filter_dict or "", parameters=params or {}
        )
        records: List[Dict[str, Any]] = []
        for record in result:
            converted: Dict[str, Any] = {}
            for key, value in record.items():
                converted[key] = _convert_neo4j_value(value)
            records.append(converted)
        return records

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
        atomically using a Neo4j transaction.

        All operations run inside a single ``begin_transaction``.
        If any step fails the transaction is rolled back.

        Args:
            strong_node: The root (non-weak) node of the group.
            weak_nodes: Optional list of WeakNode instances.
            weak_relations: Optional list of WeakRelation instances.

        Returns:
            The Neo4j internal id of the ``strong_node``.

        Raises:
            RuntimeError: If any part of the group creation fails.
        """
        for lbl in strong_node.labels:
            _validate_cypher_identifier(lbl, "label")
        for wn in weak_nodes or []:
            for lbl in wn.labels:
                _validate_cypher_identifier(lbl, "label")
            parent_relation = wn["parent_relation"] if "parent_relation" in wn else "HAS_CHILD"
            _validate_cypher_identifier(parent_relation, "relation type")
        for wr in weak_relations or []:
            _validate_cypher_identifier(wr["type"], "relation type")
            for lbl in (wr["src"].get("main_label"), wr["dst"].get("main_label")):
                if lbl:
                    _validate_cypher_identifier(lbl, "main_label")

        session = self._session

        def _create_group_tx(tx: Any) -> int:
            # 1. Create the strong node
            pk, attributes = strong_node.attributes
            props = attributes if pk is None else {**pk, **attributes}
            labels = "" if strong_node.labels == "" else ":" + ":".join(strong_node.labels)

            result = tx.run(
                "CREATE (a" + labels + ") SET a = $prop_dict RETURN id(a) AS id",
                prop_dict=props,
            ).value("id")[0]
            strong_node["neo4j_id"] = result

            # 2. Create weak nodes and their parent relations
            if weak_nodes:
                for wn in weak_nodes:
                    wn_pk, wn_attrs = wn.attributes
                    wn_props = wn_attrs if wn_pk is None else {**wn_pk, **wn_attrs}
                    wn_labels = "" if wn.labels == "" else ":" + ":".join(wn.labels)

                    wn_result = tx.run(
                        "CREATE (a" + wn_labels + ") SET a = $prop_dict RETURN id(a) AS id",
                        prop_dict=wn_props,
                    ).value("id")[0]
                    wn["neo4j_id"] = wn_result

                    # Create the WeakRelation edge from strong_node to weak node
                    rel_type = wn.get("parent_relation", "HAS_CHILD")
                    tx.run(
                        "MATCH (a) WHERE id(a) = $parent_id "
                        "MATCH (b) WHERE id(b) = $child_id "
                        "CREATE (a)-[r:" + rel_type + "]->(b) "
                        "SET r._propagate = TRUE, r.parent_relation = $rel_type",
                        parent_id=strong_node["neo4j_id"],
                        child_id=wn_result,
                        rel_type=rel_type,
                    )

            # 3. Create additional weak relations
            if weak_relations:
                for wr in weak_relations:
                    wr_src = wr["src"]
                    wr_dst = wr["dst"]
                    wr_type = wr["type"]
                    wr_attrs = wr["attributes"] or {}

                    src_id = wr_src.get("neo4j_id")
                    dst_id = wr_dst.get("neo4j_id")

                    # Resolve by neo4j_id if available, otherwise by PK
                    if src_id is not None and dst_id is not None:
                        tx.run(
                            "MATCH (a) WHERE id(a) = $src "
                            "MATCH (b) WHERE id(b) = $dst "
                            "CREATE (a)-[r:" + wr_type + "]->(b)",
                            src=src_id,
                            dst=dst_id,
                        )
                    else:
                        # Fallback: resolve by PK
                        src_pk = wr_src.get("pk", {})
                        dst_pk = wr_dst.get("pk", {})
                        src_label = wr_src.get("main_label", "Node")
                        dst_label = wr_dst.get("main_label", "Node")
                        query = (
                            "MATCH (a:" + src_label + ") WHERE "
                            + _generate_where_cond("a", src_pk)
                            + " MATCH (b:" + dst_label + ") WHERE "
                            + _generate_where_cond("b", dst_pk)
                            + " CREATE (a)-[r:" + wr_type + "]->(b)"
                        )
                        if wr_attrs:
                            query += " SET r = $prop_dict"
                            tx.run(query, prop_dict=wr_attrs)
                        else:
                            tx.run(query)

            # 4. Mark the strong node as having its weak children initialized.
            tx.run(
                "MATCH (a) WHERE id(a) = $nid SET a._weak_init_done = TRUE",
                nid=result,
            )

            return result

        try:
            return self._write_transaction(session, _create_group_tx)
        except ConstraintError as err:
            raise RuntimeError("Duplicate key: " + err.message) from err
        except TransactionError as err:
            raise RuntimeError("Transaction failed: " + str(err)) from err

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
            session = self._session

            # Step 1: Find strong nodes whose weak children have NOT been
            # initialized yet (_weak_init_done is NULL or FALSE).
            # Skip those that already have it set — create_group() marks
            # it automatically because the edges already carry _propagate=True.
            result = session.run(
                "MATCH (n) WHERE n._weak_init_done IS NULL OR n._weak_init_done = FALSE "
                "RETURN id(n) AS nid"
            )
            pending_strong_ids = {record["nid"] for record in result}

            # Step 2: Mark all nodes that are children of a _propagate edge,
            # but only if their parent is in the pending set.
            if pending_strong_ids:
                placeholders = ", ".join(f"$p{i}" for i in range(len(pending_strong_ids)))
                query = (
                    "MATCH (a)-[r]->(b) WHERE r._propagate = TRUE "
                    f"AND id(a) IN [{placeholders}] "
                    "RETURN DISTINCT id(b) AS child_id, id(a) AS parent_id"
                )
                child_result = session.run(
                    query,
                    **{f"p{i}": nid for i, nid in enumerate(pending_strong_ids)},
                )
                child_ids = {record["child_id"] for record in child_result}

                for nid in child_ids:
                    session.run(
                        "MATCH (n) WHERE id(n) = $nid SET n.is_weak = TRUE, n._propagate = TRUE",
                        nid=nid,
                    )

                # Also set parent_relation on the edges
                for record in child_result:
                    parent_id = record["parent_id"]
                    child_id = record["child_id"]
                    session.run(
                        "MATCH (a)-[r]->(b) "
                        "WHERE id(a) = $parent AND id(b) = $child "
                        "AND r._propagate = TRUE "
                        "SET r.parent_relation = type(r)",
                        parent=parent_id,
                        child=child_id,
                    )

                # Mark the parent as initialized
                for nid in pending_strong_ids:
                    session.run(
                        "MATCH (n) WHERE id(n) = $nid SET n._weak_init_done = TRUE",
                        nid=nid,
                    )
            else:
                child_ids = set()

            # Step 3: Mark all edges from WeakNodes that have _propagate
            # (for edges that weren't caught by the parent-child scan above)
            session.run(
                "MATCH (a)-[r]->(b) WHERE b.is_weak = TRUE AND r._propagate IS NULL "
                "SET r._propagate = TRUE"
            )

            # Step 4: Count nodes for progress
            count_result = session.run("MATCH (n) RETURN count(n) AS total")
            total = count_result.single()["total"]

            if progress_callback:
                progress_callback(total, total)

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
        if self._tx is not None:
            if not self._tx.closed():
                self._tx.close()
            self._tx = None
        self._closed = True
        self._fk_index.clear()
        self._driver.close()

    def enable_vector_index(
        self, name: str, dimensions: int, space: str = "cosine", **kwargs
    ) -> None:
        """Neo4j backend does not support vector indexes."""
        raise NotImplementedError(
            "Vector indexes are not supported by Neo4jGraph. "
            "Use NetworkXGraph for vector search."
        )

    def query_vector_index(
        self, name: str, vector: List[float], top_k: int = 5
    ) -> List[int]:
        """Neo4j backend does not support vector queries."""
        raise NotImplementedError(
            "Vector queries are not supported by Neo4jGraph. "
            "Use NetworkXGraph for vector search."
        )

    def get_subdocuments(self, strong_node: Node) -> List[Dict[str, Any]]:
        """Return all subdocuments (WeakNodes) reachable from *strong_node*
        through ``_propagate`` edges.

        Follows edges in their declared direction (parent → child) and
        returns every descendant WeakNode as a dict with ``label``, ``pk``,
        and ``properties`` keys.

        Args:
            strong_node: The root (non-weak) node.

        Returns:
            A list of dicts, one per subdocument.
        """
        if self._closed:
            raise RuntimeError("Cannot query a closed Neo4jGraph.")

        pk = strong_node._primary_key
        if pk is None:
            return []

        # Build MATCH WHERE clause from primary key
        where_parts = []
        params = {}
        for key, val in pk.items():
            if key == "neo4j_id":
                where_parts.append("strong.neo4j_id = $strong_id")
                params["strong_id"] = val
            else:
                where_parts.append(f"strong.{key} = $pk_{key}")
                params[f"pk_{key}"] = val
        where_clause = " AND ".join(where_parts)

        # Recursive match: follow _propagate edges parent → child
        query = (
            "MATCH path = (strong)-[*]->(subdoc) "
            f"WHERE {where_clause} "
            "AND ALL(r IN relationships(path) WHERE r._propagate = TRUE) "
            "RETURN subdoc"
        )

        result = self._session.run(query, parameters=params)
        subdocuments: List[Dict[str, Any]] = []
        seen_pks: set = set()

        for record in result:
            subdoc = record.get("subdoc")
            if subdoc is None:
                continue

            # Convert dict format (driver 6.x) or native object (driver 4.x)
            if isinstance(subdoc, dict):
                label = list(subdoc.get("labels", ["?"]))[-1]
                props = subdoc.get("properties", {})
            elif hasattr(subdoc, "labels"):
                label = list(subdoc.labels)[-1]
                props = dict(subdoc)
            else:
                continue

            # Extract PK: remove internal properties (neo4j_id, _weak_init_done,
            # is_weak, parent_relation) and keep the business PK
            business_pk = {
                k: v for k, v in props.items()
                if not k.startswith("_") and k not in ("neo4j_id", "is_weak")
            }

            # Deduplicate by PK
            pk_key = (label, frozenset(business_pk.items()))
            if pk_key in seen_pks:
                continue
            seen_pks.add(pk_key)

            subdocuments.append({
                "label": label,
                "pk": business_pk,
                "properties": props,
            })

        return subdocuments

    def schema_yaml(self, db_name: str) -> str:
        """Introspect the Neo4j database and return a YAML schema description.

        Queries ``db.labels()`` and ``db.relationshipTypes()`` to collect
        all labels, properties, relationship types, and counts.

        Args:
            db_name: Human-readable database name (e.g. ``"got"``).

        Returns:
            A YAML string suitable for code generation.
        """
        import datetime

        session = self._session
        # Labels
        label_results = {}
        for label_rec in session.run("CALL db.labels()"):
            label = label_rec["label"]
            count = session.run(
                f"MATCH (n:`{label}`) RETURN count(n) AS c"
            ).single()["c"]
            # Collect properties from one sample node
            props = {}
            sample = session.run(
                f"MATCH (n:`{label}`) RETURN n LIMIT 1"
            ).single()
            if sample:
                for k, v in sample["n"].items():
                    props[k] = self._python_type(v)
            label_results[label] = {"count": count, "properties": props}

        # Relationship types
        rel_results = {}
        for rel_rec in session.run("CALL db.relationshipTypes()"):
            rel_type = rel_rec["relationshipType"]
            count = session.run(
                f"MATCH ()-[r:`{rel_type}`]->() RETURN count(r) AS c"
            ).single()["c"]
            # Collect src/dst labels and properties
            src_labels = set()
            dst_labels = set()
            props = {}
            sample = session.run(
                f"MATCH (a)-[r:`{rel_type}`]->(b) RETURN a, r, b LIMIT 1"
            ).single()
            if sample:
                for k in sample["a"]:
                    if k not in ("element_id",):
                        src_labels.add(sample["a"].get("labels", ("Node",))[0])
                for k in sample["b"]:
                    if k not in ("element_id",):
                        dst_labels.add(sample["b"].get("labels", ("Node",))[0])
                for k, v in sample["r"].items():
                    if k not in ("element_id",):
                        props[k] = self._python_type(v)
            rel_results[rel_type] = {
                "count": count,
                "src": sorted(src_labels),
                "dst": sorted(dst_labels),
                "properties": props,
            }

        # Build YAML
        lines: List[str] = []
        lines.append(f"# Schema generated from: {db_name}")
        lines.append(f"# Generated at: {datetime.datetime.now().strftime('%Y-%m-%d')}")
        lines.append("")

        lines.append("labels:")
        if not label_results:
            lines.append("  {}")
        else:
            for label in sorted(label_results):
                info = label_results[label]
                props = info["properties"]
                count = info["count"]
                pk_fields = [k for k in props if k not in ("pk", "main_label", "labels")]
                pk_str = f"[{', '.join(repr(f) for f in pk_fields[:2])}]" if pk_fields else "[]"

                lines.append(f"  {label}:")
                lines.append(f"    class_name: {label[0].upper() + label[1:] if label else 'Node'}")
                lines.append(f"    base_class: Node")
                lines.append(f"    properties:")
                if not props:
                    lines.append(f"      {{}}")
                else:
                    for prop_name, prop_type in sorted(props.items()):
                        lines.append(f"      {prop_name}: {prop_type}")
                lines.append(f"    primary_key: {pk_str}")
                lines.append(f"    count: {count}")

        lines.append("")
        lines.append("relationships:")
        if not rel_results:
            lines.append("  {}")
        else:
            for rel_type in sorted(rel_results):
                info = rel_results[rel_type]
                props = info["properties"]
                class_name = "".join(w.capitalize() for w in rel_type.lower().split("_"))

                lines.append(f"  {rel_type}:")
                lines.append(f"    class_name: {class_name}")
                lines.append(f"    src: {', '.join(info['src']) if info['src'] else 'Node'}")
                lines.append(f"    dst: {', '.join(info['dst']) if info['dst'] else 'Node'}")
                lines.append(f"    properties:")
                if not props:
                    lines.append(f"      {{}}")
                else:
                    for prop_name, prop_type in sorted(props.items()):
                        lines.append(f"      {prop_name}: {prop_type}")
                lines.append(f"    count: {info['count']}")

        # WeakRelations section (empty — must be inferred from application logic)
        lines.append("")
        lines.append("weak_relations:")
        lines.append("  {}")

        return "\n".join(lines) + "\n"

    def _python_type(self, value: Any) -> str:
        """Map a Neo4j value to a YAML/Python type string."""
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "float"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "array"
        if isinstance(value, dict):
            return "object"
        if value is None:
            return "null"
        return "string"

    # ------------------------------------------------------------------
    # Protected helpers: node operations
    # ------------------------------------------------------------------

    def _insertNode(
        self,
        node: Node,
        update: bool = False,
        replace: bool = False,
    ) -> int:
        # Validate every label (main_label + alternative_labels), not just
        # main_label — _create_node/_update_node splice the full label list
        # into the Cypher CREATE/MERGE text. This also covers nodes reached
        # via the dependencies mechanism, which calls _insertNode directly
        # and would otherwise bypass insertNode()'s own check.
        for lbl in node.labels:
            if lbl:
                _validate_cypher_identifier(lbl, "label")

        node.version = self._version

        # check if node is weak if so, check if  its  parent node is already inserted. If no, raise an exception and cancel the transaction
        # try:
        if node["is_weak"]:
            if self.checkNode(node["parent"]) is None:
                raise Exception(
                    "CVCDocDB Exception: missing parent node  "
                    + str(node["parent"])
                    + ". Insert it before "
                    + str(node)
                    + ". Node is weak:"
                    + str(node["is_weak"])
                    + ". Parent relation:"
                    + str(node["parent_relation"])
                )

        # pk, attributes = node.attributes
        # Check if node already exists
        _trasa = ""
        try:
            # NB: Neo4j-side uniqueness constraints (NODE KEY) were tried
            # historically and reverted — the same main_label is reused
            # across unrelated tests/callers with different pk shapes, so a
            # DB-level constraint on one shape breaks inserts using another.
            # Duplicate-key detection is therefore done here, in Python,
            # mirroring NetworkXGraph's insertNode (see networkx_graph.py).

            # WeakNodes whose PK was backend-generated (pk=None originally)
            # have a PK equal to their parent's PK until they get their own
            # neo4j_id — never treat those as duplicates.
            is_backend_assigned_pk = (
                node["is_weak"]
                and node._parent is not None
                and node._parent._primary_key is not None
                and node._primary_key == node._parent._primary_key
            )

            if update:
                # id = self._session.write_transaction(self._update_node, node)
                # print("update node")
                id = self._update_node(self._tx, node)
                _trasa += "(1) Actualitza el node "
            else:
                id = self.checkNode(node) if not is_backend_assigned_pk else None
                if isinstance(id, int) and not isinstance(id, bool):
                    if replace:
                        # ON UPDATE CASCADE: in Neo4j, edges reference nodes
                        # by internal ID which never changes.  replace=True
                        # deletes the node (with detach, cascading edges) and
                        # creates a fresh one — the caller must recreate
                        # relations if needed.
                        self.deleteNode(node, detach=True, propagation=True)
                        _trasa += "(1) esborra el node "
                    else:
                        raise RuntimeError(
                            f"Duplicate key: node with pk={node._primary_key} "
                            f"and main_label={node.main_label} already exists "
                            f"(id={id}). Use replace=True to overwrite or "
                            f"update=True to merge attributes."
                        )
                # id = self._session.write_transaction(self._create_node, node)
                # print("create node")
                id = self._create_node(self._tx, node)
                _trasa += "(2) crea  el node "

            # If _create_node returned None (ConstraintError caught silently),
            # fall back to a lookup by label + PK to retrieve the existing
            # node's Neo4j internal id.  Without this, weak nodes whose parent
            # was just inserted would have neo4j_id=None and the subsequent
            # insertRelation FK check would fail.
            if id is None:
                id = self._check_node(
                    self._tx, node["main_label"], node["pk_attributes"]
                )
                if id is not None:
                    _trasa += "(3) recupera el node existent "

            node["neo4j_id"] = id

            # Si el node tenia pk=None explícit, assignem l'ID generat com a PK
            if node._primary_key is None:
                node._primary_key = {"id": id}

            # Track neo4j_id -> pk mapping for get_node_pks()
            if node._primary_key is not None:
                self._node_pks[id] = dict(node._primary_key)

            # if is a weak entity the edge linking the parent node must be created
            if node["is_weak"]:
                # print("node", node.main_label, node.neo4j_id, "is weak")
                # Skip PK validation for nodes whose PK was backend-generated (pk=None originally).
                # Such nodes have a simple {"id": backend_id} PK that won't contain parent PK keys.
                child_pk = node["pk"]["pk"]
                if not (isinstance(child_pk, dict) and len(child_pk) == 1 and "id" in child_pk):
                    for ppk in node["parent"]["pk"]["pk"]:
                        if not node["parent"]["pk"]["pk"][ppk] == node["pk"]["pk"][ppk]:
                            raise RuntimeError(
                                "CVCDocDB Exception: Integrity Constraint Violated. Child node keys does not reference proper parent keys"
                            )

                self.insertRelation(
                    WeakRelation(
                        node["parent"],
                        node,
                        node["parent_relation"],
                        propagate=True,
                    ),
                    update=True,
                    replace=False,
                )

        except ConstraintError as err:
            warnings.warn(f"[CVCDocDB Message]: {err.message}")
            id = self.checkNode(node)
            return id
            # raise RuntimeError("CVCDocDB Exception: " + err.message + " " + _trasa) from err

        return id

    def _resolve_neo4j_id(
        self,
        tx: Any,
        node_data: Dict[str, Any],
    ) -> Optional[int]:
        """Look up the Neo4j internal id for a node by its label and primary key."""
        pk = node_data.get("pk")
        label = node_data.get("main_label")
        if pk is None or label is None:
            return None
        _validate_cypher_identifier(label, "main_label")
        result = tx.run(
            "MATCH (n:"
            + label
            + ") WHERE "
            + _generate_where_cond("n", pk)
            + " RETURN id(n) AS nid"
        ).single()
        return result["nid"] if result else None

    # ------------------------------------------------------------------
    # Protected helpers: FK index operations
    # ------------------------------------------------------------------

    def _add_to_fk_index(
        self, src_id: int, dst_id: int, rel_type: str
    ) -> None:
        """Add a relation to the FK index."""
        self._fk_index.setdefault(src_id, set()).add((dst_id, rel_type, "out"))
        self._fk_index.setdefault(dst_id, set()).add((src_id, rel_type, "in"))

    def _remove_from_fk_index(self, node_id: int) -> None:
        """Remove all FK index entries for a given node."""
        self._fk_index.pop(node_id, None)
        for other_id in list(self._fk_index):
            self._fk_index[other_id] = {
                entry for entry in self._fk_index[other_id] if entry[0] != node_id
            }
            if not self._fk_index[other_id]:
                del self._fk_index[other_id]

    def _cascade_delete(self, tx: Any, node: Node) -> None:
        """ON DELETE CASCADE: delete all edges connected to the node.

        Uses the FK index to find all edges connected to the node and removes them.
        Neighbor nodes are left as standalone entities.
        """
        node_id = node.neo4j_id
        if node_id is None:
            return

        # Find all connected nodes via the FK index
        entries = self._fk_index.get(node_id, set()).copy()
        for neighbor_id, rel_type, direction in entries:
            # Delete the edge
            if direction == "out":
                edge_query = (
                    "MATCH (a)-[r:" + rel_type + "]->(b) "
                    "WHERE id(a) = " + str(node_id) + " AND id(b) = " + str(neighbor_id)
                    + " DELETE r"
                )
            else:
                edge_query = (
                    "MATCH (a)-[r:" + rel_type + "]->(b) "
                    "WHERE id(b) = " + str(node_id) + " AND id(a) = " + str(neighbor_id)
                    + " DELETE r"
                )
            tx.run(edge_query)

            # Clean FK index for neighbor
            if neighbor_id in self._fk_index:
                self._fk_index[neighbor_id] = {
                    e for e in self._fk_index[neighbor_id]
                    if e != (node_id, rel_type, "in" if direction == "out" else "out")
                }
                if not self._fk_index[neighbor_id]:
                    del self._fk_index[neighbor_id]
        self._fk_index.pop(node_id, None)

    def _remove_from_fk_index_for_relation(
        self, tx: Any, rel: Relation
    ) -> None:
        """Remove FK index entries for a specific relation."""
        src_id = self._resolve_neo4j_id(tx, rel["src"])
        dst_id = self._resolve_neo4j_id(tx, rel["dst"])
        rel_type = rel["type"]
        if src_id is not None:
            self._fk_index[src_id] = {
                entry for entry in self._fk_index.get(src_id, set())
                if entry != (dst_id, rel_type, "out")
            }
            if not self._fk_index[src_id]:
                del self._fk_index[src_id]
        if dst_id is not None:
            self._fk_index[dst_id] = {
                entry for entry in self._fk_index.get(dst_id, set())
                if entry != (src_id, rel_type, "in")
            }
            if not self._fk_index[dst_id]:
                del self._fk_index[dst_id]

    def _update_fk_index_for_relation(
        self, tx: Any, rel: Relation
    ) -> None:
        """Look up node IDs and add the relation to the FK index."""
        src_id = self._resolve_neo4j_id(tx, rel["src"])
        dst_id = self._resolve_neo4j_id(tx, rel["dst"])
        if src_id is not None:
            self._add_to_fk_index(src_id, dst_id or 0, rel["type"])
        if dst_id is not None:
            self._add_to_fk_index(src_id or 0, dst_id, rel["type"])

    # ------------------------------------------------------------------
    # Static helpers: node operations
    # ------------------------------------------------------------------

    @staticmethod
    def _create_node(tx: Any, node: Node) -> Optional[int]:
        pk, attributes = node.attributes
        props = attributes if pk is None else {**pk, **attributes}

        labels = "" if node.labels == "" else ":" + ":".join(node.labels)

        try:
            result = tx.run(
                "CREATE (a" + labels + ") SET a = $prop_dict RETURN id(a) AS id",
                prop_dict=props,
            ).value("id")[0]
            return result
        except ConstraintError as err:
            print("[CVCDocDB Message]: " + err.message)
            print("node already inserted")
            return None

    @staticmethod
    def _update_node(tx: Any, node: Node) -> int:
        pk, attributes = node.attributes
        # print("(_update_node) pk:", pk)
        # print("(_update_node) attributes:", attributes)

        main_label = "" if node.main_label == "" else ":" + node.main_label
        labels = "" if node.labels == "" else ":" + ":".join(node.labels)

        try:
            query = (
                "MERGE (a"
                + main_label
                + " { "
                + _generate_where_cond("a", pk, type="merge")
                + " })"
            )
        except:
            pass
        # TODO: Fix lines below
        # if labels != '':
        #    query += " SET a" + labels + " "

        a = {**pk, **attributes} if len(attributes) > 0 else pk
        # print("(_update_node) a:", a)
        query += (
            " ON CREATE SET a"
            + labels
            + ", a = $prop_dict"
            + " ON MATCH SET a"
            + labels
            + ", a += $prop_dict"
            + " RETURN id(a) AS id"
        )
        # print("(_update_node) query:", query, "a:", a)
        try:
            result = tx.run(query, prop_dict=a).value("id")[0]
        except Exception as err:
            print(query)
            print(a)
            raise
        # print("(_update_node) result:", result)
        return result

    @staticmethod
    def _delete_node(tx: Any, node: Node, detach: bool = False) -> bool:
        neo4j_id = node.neo4j_id
        detach_text = " detach " if detach else ""
        if neo4j_id is None:
            pk, attributes = node.attributes
            main_label = "" if node.main_label == "" else ":" + node.main_label

            query = (
                "MATCH (a"
                + main_label
                + ") WHERE "
                + _generate_where_cond("a", pk)
                + " "
                + detach_text
                + "DELETE a"
            )
        else:
            query = (
                "MATCH (a) WHERE id(a) = " + str(neo4j_id) + " " + detach_text + "DELETE a"
            )

        result = tx.run(query).values()
        return result == []

    @staticmethod
    def _check_node(
        tx: Any, main_label: str, id: Dict[str, str]
    ) -> Optional[int]:
        if id is None:
            return None

        try:
            idnode = tx.run(
                "MATCH (a:"
                + main_label
                + ") WHERE "
                + _generate_where_cond("a", id)
                + " RETURN id(a)"
            ).single()
        except:
            return None

        if idnode is not None:
            return idnode.value()

        return None

    @staticmethod
    def _check_node_by_id(
        tx: Any, main_label: str, node_id: int
    ) -> Optional[int]:
        try:
            idnode = tx.run(
                "MATCH (a:"
                + main_label
                + ") WHERE id(a) = $nid RETURN id(a)",
                nid=node_id,
            ).single()
        except Exception:
            return None

        if idnode is not None:
            return idnode.value()

        return None

    # ------------------------------------------------------------------
    # Static helpers: relation operations
    # ------------------------------------------------------------------

    @staticmethod
    def _create_relation(tx: Any, rel: Relation) -> int:
        rel_type = rel["type"]
        src = rel["src"]
        dst = rel["dst"]

        attributes = rel["attributes"]

        query = (
            "MATCH (a:"
            + src["main_label"]
            + ") WHERE "
            + _generate_where_cond("a", src["pk"])
            + "MATCH (b:"
            + dst["main_label"]
            + ") WHERE  "
            + _generate_where_cond("b", dst["pk"])
            + "CREATE (a)-[r:"
            + rel_type
            + "]->(b)"
        )

        if attributes is not None:
            result = tx.run(
                query + " SET r = $prop_dict RETURN id(r) AS id",
                prop_dict=attributes,
            ).value("id")[0]
        else:
            try:
                result = tx.run(query + " RETURN id(r) AS id").value("id")[0]
            except Exception as err:
                a = tx.run("PROFILE " + query + " return id(r) as id")
                print(a.consume().profile["args"]["string-representation"])
                print(type(a))
                print(err)
                print(query)
                print(src)
                print(dst)
                print(rel_type)
                raise

        return result

    @staticmethod
    def _update_relation(tx: Any, rel: Relation) -> int:
        src, dst, type = rel["src"], rel["dst"], rel["type"]
        attributes = rel["attributes"]

        query = (
            "MATCH (a:"
            + src["main_label"]
            + " {"
            + _generate_where_cond("a", src["pk"], type="merge")
            + " }) "
            + "MERGE (b:"
            + dst["main_label"]
            + " {"
            + _generate_where_cond("b", dst["pk"], type="merge")
            + " }) "
            "MERGE (a)-[r:" + type + "]-> (b)"
        )
        if attributes:
            try:
                result = tx.run(
                    query
                    + " ON CREATE SET r += $prop_dict"
                    + " ON MATCH SET r += $prop_dict"
                    + " RETURN id(r) AS id",
                    prop_dict=attributes,
                ).value("id")[0]
            except Exception as err:
                print(err)
                print(query)
                print(attributes)
                print(src)
                print(dst)
                print(type)
                raise
        else:
            try:
                result = tx.run(query + " RETURN id(r) AS id").value("id")[0]
            except Exception as err:
                print(err)
                print(query)
                print(src)
                print(dst)
                print(type)
                raise

        return result

    @staticmethod
    def _delete_relation(tx: Any, rel: Relation) -> Optional[bool]:
        src, dst, type = rel["src"], rel["dst"], rel["type"]

        query = (
            "MATCH (a:"
            + src["main_label"]
            + " {"
            + _generate_where_cond("a", src["pk"], type="merge")
            + " })"
            + "-[r:"
            + type
            + "]->"
            + "(b:"
            + dst["main_label"]
            + " {"
            + _generate_where_cond("b", dst["pk"], type="merge")
            + " })"
            + "DELETE r"
        )

        result = tx.run(query).single()
        if result is None:
            return False
        else:
            return result.value()

    @staticmethod
    def _check_relation(
        tx: Any,
        src: Dict[str, Union[str, int, Dict[str, Union[str, int]]]],
        dst: Dict[str, Union[str, int, Dict[str, Union[str, int]]]],
        rel_type: str = "None",
    ) -> Optional[int]:
        if src.get("pk") is None:
            return None

        if dst.get("pk") is None:
            return None

        # TODO: Cal revisar que passa quan pk es ne4jid
        query = (
            "MATCH (a:"
            + src["main_label"]
            + ")-[r:"
            + rel_type
            + "]->(b:"
            + dst["main_label"]
            + ") "
            + "MATCH (a:"
            + src["main_label"]
            + ") WHERE "
            + _generate_where_cond("a", src["pk"])
            + "MATCH (b:"
            + dst["main_label"]
            + ") WHERE  "
            + _generate_where_cond("b", dst["pk"])
            + "RETURN id(r) AS id"
        )

        idnode = tx.run(query).single()

        if idnode is not None:
            return idnode.value("id")

        return None

    @staticmethod
    def _check_relation_by_id(
        tx: Any,
        src: Dict[str, Union[str, int, Dict[str, Union[str, int]]]],
        dst: Dict[str, Union[str, int, Dict[str, Union[str, int]]]],
        rel_type: str = "None",
    ) -> Optional[int]:
        if src.get("pk") is None:
            return None

        if dst.get("pk") is None:
            return None

        # TODO: Cal revisar que passa quan pk es ne4jid
        query = (
            "MATCH (a:"
            + src["main_label"]
            + ")-[r:"
            + rel_type
            + "]->(b:"
            + dst["main_label"]
            + ") "
            + "MATCH (a:"
            + src["main_label"]
            + ") WHERE "
            + _generate_where_cond("a", src["pk"])
            + "MATCH (b:"
            + dst["main_label"]
            + ") WHERE  "
            + _generate_where_cond("b", dst["pk"])
            + "RETURN id(r) AS id"
        )

        idnode = tx.run(query).single()

        if idnode is not None:
            return idnode.value("id")

        return None

    # ------------------------------------------------------------------
    # Static helpers: constraint / query helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_fk(
        tx: Any,
        node_data: Dict[str, Any],
        label: str,
    ) -> bool:
        """Verify that a node referenced by a relation exists in the database.

        This enforces foreign-key consistency: a relation can only connect
        nodes that already exist.  Mirrors the relational constraint
        ``ON DELETE RESTRICT`` / ``ON UPDATE CASCADE`` for graph edges.

        Returns True if the node exists, False otherwise.
        """
        if node_data.get("pk") is None:
            return False
        if node_data.get("main_label") is None:
            return False
        return tx.run(
            "MATCH (n:"
            + node_data["main_label"]
            + ") WHERE "
            + _generate_where_cond("n", node_data["pk"])
            + " RETURN count(n) > 0 AS found"
        ).single()["found"]

    @staticmethod
    def _create_constraint(tx: Any, main_label: str, id: List[str]) -> None:
        # Check whether an index (unique) exists for each node label. If not, it creates one to ensure unicity
        try:
            fields = ".".join([f"c.{f}" for f in id])
            query = (
                "CREATE CONSTRAINT " + main_label + "_PK IF NOT EXISTS "
                "FOR (c:" + main_label + ") REQUIRE c." + fields + " IS NODE KEY"
            )

            result = tx.run(query)
        except ConstraintError as err:
            print("[CVCDocDB Message]: " + err.message)
            pass

    @staticmethod
    def _has_no_edges(tx: Any, node: Node) -> bool:
        """Check if a node has no connected edges (for ON DELETE RESTRICT)."""
        neo4j_id = node.neo4j_id
        if neo4j_id is not None:
            query = (
                "MATCH (n) WHERE id(n) = "
                + str(neo4j_id)
                + " OPTIONAL MATCH (n)-[x]-() RETURN count(x) = 0 AS has_no_edges"
            )
        else:
            main_label = "" if node.main_label == "" else ":" + node.main_label
            query = (
                "MATCH (a"
                + main_label
                + ") WHERE "
                + _generate_where_cond("a", node["pk"])
                + " OPTIONAL MATCH (a)-[x]-() RETURN count(x) = 0 AS has_no_edges"
            )
        return tx.run(query).single()["has_no_edges"]

    @staticmethod
    def _get_propagated_nodes(tx: Any, node: Node) -> List[Any]:
        """Get child nodes that should be deleted when this node is deleted.

        Matches edges where r._propagate=TRUE (set on WeakNode relations).
        """
        neo4j_id = node.neo4j_id
        if neo4j_id is None:
            main_label = "" if node.main_label == "" else ":" + node.main_label
            query = (
                "MATCH (a"
                + main_label
                + ")-[r]->(b) WHERE r._propagate=TRUE AND "
                + _generate_where_cond("a", node["pk_attributes"])
                + " RETURN b"
            )
        else:
            query = (
                "MATCH (n)-[r]->(b) WHERE id(n)="
                + str(neo4j_id)
                + " AND r._propagate=TRUE RETURN b"
            )
        return tx.run(query).values()


def _convert_neo4j_value(value: Any) -> Any:
    """Convert a Neo4j value to a plain Python dict/list/primitive.

    - ``Node`` → ``{"labels": [...], "properties": {...}}``
    - ``Relationship`` → ``{"type": ..., "start_node": ..., "end_node": ..., "properties": ...}``
    - ``Path`` → ``{"nodes": [...], "relationships": [...], "length": ...}``
    - ``list`` → recursively convert each element
    - Everything else → returned as-is
    """
    if value is None:
        return None
    if hasattr(value, "labels") and hasattr(value, "properties"):
        # Neo4j Node
        return {
            "labels": list(value.labels),
            "properties": dict(value),
        }
    if hasattr(value, "type") and hasattr(value, "start_node"):
        # Neo4j Relationship
        return {
            "type": value.type,
            "start_node": int(value.start_node),
            "end_node": int(value.end_node),
            "properties": dict(value),
        }
    if hasattr(value, "nodes") and hasattr(value, "relationships"):
        # Neo4j Path
        return {
            "nodes": [_convert_neo4j_value(n) for n in value.nodes],
            "relationships": [_convert_neo4j_value(r) for r in value.relationships],
            "length": value.length,
        }
    if isinstance(value, (list, tuple)):
        return [_convert_neo4j_value(v) for v in value]
    return value


def _generate_tuple(node_name, pk, with_values=False):
    if with_values:
        return (
            " (" + ",".join([node_name + ".{}={}".format(k, pk[k]) for k in pk]) + ") "
        )
    else:
        return " (" + ",".join([node_name + ".{}".format(k) for k in pk]) + ") "


def _generate_where_cond(node_name, pk, type="where"):
    valor = [pk[a] for a in pk.keys() if a == 'neo4j_id']
    valor = valor[0] if len(valor) > 0 else None

    id_key = 'id(' + node_name + ')'
    if valor is not None and len(pk) == 1:
        pk = { id_key : valor}

    if type.lower() == "where":
        conj, equal = " AND ", "="
        node_name += "."
    if type.lower() == "merge":
        conj, equal = " , ", " : "
        node_name = ""

    def _safe_value(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        return '"' + _escape_cypher_string(str(v)) + '"'

    return (
        " "
        + conj.join(
            [
                node_name
                + "{}{}{}".format(
                    k if k == id_key else _validate_cypher_identifier(k, "property key"),
                    equal,
                    _safe_value(pk[k]),
                )
                for k in pk
            ]
        )
        + " "
    )
