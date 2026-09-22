"""Generic backend-to-backend graph migration.

Copies an entire graph — nodes, then edges, then vector indexes — from
one :class:`~cvcdocdb.graph_store.GraphStore` into another (e.g.
``NetworkXGraph`` → ``Neo4jGraph`` or the reverse), using only the
common ``GraphStore`` interface. It doesn't know anything about
``WeakNode``, ``Individu``/``be_value_properties``, or any other
Python-level entity class — it works at the plain node/edge level, so
it works for any two backend implementations, current or future.

Quick-start::

    from cvcdocdb import NetworkXGraph, Neo4jGraph
    from cvcdocdb.migration import migrate

    source = NetworkXGraph("archive.pkl")
    target = Neo4jGraph("bolt://localhost:7687", "neo4j", "secret")
    stats = migrate(source, target)
    print(stats)  # MigrationStats(nodes_migrated=120, edges_migrated=340, ...)
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Tuple, Union

from .base import Node, Relation
from .graph_store import GraphStore
from .networkx_graph import _match as _nx_match

_DEFAULT_CHUNK_SIZE = 500


@dataclass
class MigrationStats:
    """Counters describing a completed (or in-progress) migration."""

    nodes_migrated: int = 0
    nodes_skipped: int = 0
    edges_migrated: int = 0
    edges_skipped: int = 0
    indexes_migrated: int = 0
    indexes_skipped: int = 0
    #: One entry per node/edge/index that raised and was skipped instead
    #: of aborting the whole migration (only populated when
    #: ``on_error="skip"``).
    errors: List[str] = field(default_factory=list)


def migrate(
    source: GraphStore,
    target: GraphStore,
    label_filter: Optional[Union[str, List[str]]] = None,
    property_filter: Optional[Dict[str, Any]] = None,
    update: bool = True,
    replace: bool = False,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    on_error: str = "raise",
) -> MigrationStats:
    """Copy an entire graph from *source* into *target*, in three phases:
    **nodes**, then **edges**, then **vector indexes**.

    Generic across backends — only uses the common ``GraphStore``
    interface (``get_node_ids``, ``get_node_attrs``, ``get_edges``,
    ``get_edge_attrs``, ``insertNode``, ``insertRelation``,
    ``list_vector_indexes``, ``enable_vector_index``), so it works
    between any two implementations in either direction. Nodes are read
    and written in chunks of *chunk_size* (batched over the network for
    a ``Neo4jGraph`` source/target instead of one round-trip per node)
    so migrating a graph with many nodes/edges doesn't require holding
    the whole thing in memory or paying a per-row round-trip.

    Nodes are matched between source and target by ``(main_label, pk)``.
    Some backends (currently ``Neo4jGraph``) don't persist which
    properties form a node's primary key — reading one back from such a
    backend can't tell "this is the pk" apart from "this is a regular
    attribute". When that happens (``get_node_attrs()`` reports
    ``pk=None``), every property on that node is used as its pk instead,
    so the node is still faithfully reproduced on *target*; the only
    consequence is that a *repeated* migration run matches on the full
    property set instead of a smaller natural key.

    Edges are cascade-delete-safe: their attributes (including
    ``_propagate``) are copied as-is, so ``WeakNode``-style cascade
    delete behaves the same on *target* even though migration itself
    never constructs a ``WeakNode``.

    **Constraints/indexes**: this project deliberately does not create
    Neo4j-side ``NODE KEY``/uniqueness constraints (the same
    ``main_label`` can be used with different pk shapes by different
    callers — see the CHANGELOG), so there is no such constraint to
    migrate. The one portable "index" concept is ``NetworkXGraph``'s
    vector (ANN) index: every index reported by
    ``source.list_vector_indexes()`` is recreated on *target* via
    ``enable_vector_index()``. If *target* doesn't support vector
    indexes (e.g. a ``Neo4jGraph`` target, which always raises
    ``NotImplementedError``), that index is counted in
    ``indexes_skipped`` rather than failing the migration — the graph
    data itself has already been migrated successfully at that point.

    Args:
        source: The graph to read from.
        target: The graph to write to.
        label_filter: Keep only nodes whose ``main_label`` is in this
            set (a bare string is a singleton). ``None`` migrates every
            node. An edge is migrated only if both endpoints were kept.
        property_filter: MongoDB-style filter (same syntax as
            :class:`~cvcdocdb.torch_dataloader.GraphDataset`), applied
            on top of ``label_filter``.
        update: If True (default), MERGE + SET a node/edge that already
            exists on *target* instead of failing — makes re-running the
            migration idempotent.
        replace: If True, delete-and-recreate an existing node/edge on
            *target* instead of merging. Takes precedence over
            ``update`` when both are set (mirrors ``insertNode``).
        chunk_size: Rows read/written per round-trip when a batched path
            is available (currently: a ``Neo4jGraph`` source or target).
        on_error: ``"raise"`` (default) lets an exception from a single
            node/edge/index abort the whole migration. ``"skip"``
            records it in ``MigrationStats.errors`` and continues with
            the rest — useful for a best-effort migration of a large,
            possibly messy graph.

    Returns:
        A :class:`MigrationStats` with the counts of what was copied.
    """
    if on_error not in ("raise", "skip"):
        raise ValueError(f"on_error must be 'raise' or 'skip', got {on_error!r}")

    labels: Optional[FrozenSet[str]] = (
        frozenset([label_filter]) if isinstance(label_filter, str)
        else frozenset(label_filter) if label_filter is not None
        else None
    )
    props_filter = property_filter or {}
    stats = MigrationStats()

    # -- Phase 1: nodes -----------------------------------------------
    identities: Dict[Any, Tuple[str, Dict[str, Any]]] = {}
    node_ids = source.get_node_ids()

    with _maybe_batch(target):
        for i in range(0, len(node_ids), chunk_size):
            chunk = node_ids[i:i + chunk_size]
            for old_id, attrs in _iter_node_attrs(source, chunk):
                if attrs is None:
                    stats.nodes_skipped += 1
                    continue
                if labels is not None and attrs.get("main_label") not in labels:
                    stats.nodes_skipped += 1
                    continue
                if props_filter and not _nx_match(attrs, props_filter):
                    stats.nodes_skipped += 1
                    continue

                try:
                    main_label, pk, node_props = _split_node_attrs(attrs)
                    node_labels = attrs.get("labels") or []
                    alt_labels = [l for l in node_labels if l != main_label] or None
                    target.insertNode(
                        Node(pk=pk, main_label=main_label,
                             alternative_labels=alt_labels, **node_props),
                        update=update, replace=replace,
                    )
                except Exception as exc:  # noqa: BLE001 - re-raised unless on_error="skip"
                    if on_error == "raise":
                        raise
                    stats.errors.append(f"node {old_id}: {exc}")
                    stats.nodes_skipped += 1
                    continue

                identities[old_id] = (main_label, pk)
                stats.nodes_migrated += 1

    # -- Phase 2: edges -------------------------------------------------
    with _maybe_batch(target):
        for chunk_num, (src_id, dst_id, rel_type, edge_attrs) in enumerate(
            _iter_edges_with_attrs(source, chunk_size)
        ):
            if src_id not in identities or dst_id not in identities:
                stats.edges_skipped += 1
                continue
            try:
                src_label, src_pk = identities[src_id]
                dst_label, dst_pk = identities[dst_id]
                src_node = Node(pk=src_pk, main_label=src_label)
                dst_node = Node(pk=dst_pk, main_label=dst_label)
                target.insertRelation(
                    Relation(src_node, dst_node, rel_type, **edge_attrs),
                    update=update, replace=replace,
                )
            except Exception as exc:  # noqa: BLE001
                if on_error == "raise":
                    raise
                stats.errors.append(f"edge {src_id}->{dst_id} [{rel_type}]: {exc}")
                stats.edges_skipped += 1
                continue
            stats.edges_migrated += 1

    # -- Phase 3: vector indexes ----------------------------------------
    for index_meta in source.list_vector_indexes():
        try:
            target.enable_vector_index(**index_meta)
            stats.indexes_migrated += 1
        except NotImplementedError:
            stats.indexes_skipped += 1
        except Exception as exc:  # noqa: BLE001
            if on_error == "raise":
                raise
            stats.errors.append(f"vector index {index_meta.get('property_name')}: {exc}")
            stats.indexes_skipped += 1

    return stats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_node_attrs(
    attrs: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """Split a ``get_node_attrs()`` dict into ``(main_label, pk, other_props)``.

    Falls back to using every remaining property as the pk when the
    source backend couldn't tell us which properties are the real one
    (``attrs["pk"] is None`` — see the ``migrate()`` docstring).
    """
    main_label = attrs.get("main_label", "")
    pk = attrs.get("pk")
    props = {
        k: v for k, v in attrs.items()
        if k not in ("pk", "main_label", "labels")
    }
    if pk is None:
        pk = dict(props)
        props = {}
    return main_label, pk, props


def _maybe_batch(store: GraphStore):
    """Use *store*'s ``batch()`` transaction-grouping context manager if it
    has one (currently ``Neo4jGraph``); a no-op context otherwise."""
    batch = getattr(store, "batch", None)
    if callable(batch):
        return batch()
    return _null_context()


@contextmanager
def _null_context() -> Iterator[None]:
    yield


def _iter_node_attrs(
    source: GraphStore,
    node_ids: List[Any],
) -> Iterator[Tuple[Any, Optional[Dict[str, Any]]]]:
    """Yield ``(node_id, attrs)`` for every id in *node_ids*.

    Batches the read into a single round-trip when *source* is a
    ``Neo4jGraph`` (via its public ``query()``); falls back to one
    ``get_node_attrs()`` call per id for any other backend.
    """
    if type(source).__name__ == "Neo4jGraph" and node_ids:
        rows = source.query(
            "MATCH (n) WHERE id(n) IN $ids "
            "RETURN id(n) AS nid, labels(n) AS labels, properties(n) AS props",
            params={"ids": list(node_ids)},
        )
        found: Dict[Any, Dict[str, Any]] = {}
        for row in rows:
            node_labels = row.get("labels") or []
            props = row.get("props") or {}
            main_label = node_labels[0] if node_labels else props.get("main_label", "")
            found[row["nid"]] = {
                "pk": None,
                "main_label": main_label,
                "labels": node_labels,
                **props,
            }
        for nid in node_ids:
            yield nid, found.get(nid)
        return

    for nid in node_ids:
        yield nid, source.get_node_attrs(nid)


def _iter_edges_with_attrs(
    source: GraphStore,
    chunk_size: int,
) -> Iterator[Tuple[Any, Any, str, Dict[str, Any]]]:
    """Yield ``(src_id, dst_id, rel_type, attrs)`` for every edge in *source*.

    Batches the read via paginated Cypher when *source* is a
    ``Neo4jGraph`` (avoiding one ``get_edge_attrs()`` round-trip per
    edge); falls back to ``get_edges()`` + ``get_edge_attrs()`` for any
    other backend.
    """
    if type(source).__name__ == "Neo4jGraph":
        offset = 0
        while True:
            rows = source.query(
                "MATCH (a)-[r]->(b) "
                "RETURN id(a) AS src, id(b) AS dst, type(r) AS rel_type, "
                "properties(r) AS props "
                "ORDER BY id(a), id(b) SKIP $offset LIMIT $limit",
                params={"offset": offset, "limit": chunk_size},
            )
            if not rows:
                return
            for row in rows:
                yield row["src"], row["dst"], row["rel_type"], dict(row.get("props") or {})
            offset += chunk_size
        return

    for src_id, dst_id, rel_type in source.get_edges():
        yield src_id, dst_id, rel_type, (source.get_edge_attrs(src_id, dst_id, rel_type) or {})
