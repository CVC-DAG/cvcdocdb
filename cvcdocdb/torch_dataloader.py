"""PyTorch streaming dataloader for cvcdocdb graph backends.

Provides a single :class:`GraphDataset` — an
:class:`~torch.utils.data.IterableDataset` that streams nodes lazily
from any :class:`~cvcdocdb.graph_store.GraphStore` backend without
materialising the full graph in memory.

Quick-start::

    from cvcdocdb import NetworkXGraph
    from cvcdocdb.torch_dataloader import GraphDataset, GraphDataLoader

    store  = NetworkXGraph("archive.pkl")
    ds     = GraphDataset(store, label_filter="Document")
    loader = GraphDataLoader(ds, batch_size=32, num_workers=4)

    for batch in loader:
        node_ids = batch["node_id"]    # LongTensor  [B]
        labels   = batch["main_label"] # List[str]   len B

Neo4j::

    from cvcdocdb import Neo4jGraph
    from cvcdocdb.torch_dataloader import GraphDataset, GraphDataLoader

    store  = Neo4jGraph("bolt://localhost:7687", "neo4j", "secret")
    ds     = GraphDataset(store, label_filter=["Document", "Page"],
                          property_filter={"status": "indexed"},
                          chunk_size=512)
    loader = GraphDataLoader(ds, batch_size=64, num_workers=2)
"""

from __future__ import annotations

import itertools
import math
from typing import Any, Callable, Dict, FrozenSet, Iterator, List, Optional, Union

try:
    import torch
    from torch.utils.data import DataLoader, IterableDataset
except ImportError as _e:  # pragma: no cover
    raise ImportError(
        "PyTorch is required.  Install with:  pip install torch"
    ) from _e

from .graph_store import GraphStore

# Reutilitzem la lògica d'operadors MongoDB ja existent al backend NetworkX.
# Suporta: $eq $ne $gt $gte $lt $lte $in $nin $exists $regex $contains
# i combinadors lògics: $or $and $not
from .networkx_graph import _match as _nx_match

# Traducció d'operadors MongoDB → fragments Cypher parametritzats
_OP_TO_CYPHER = {
    "$eq":  "=",
    "$ne":  "<>",
    "$gt":  ">",
    "$gte": ">=",
    "$lt":  "<",
    "$lte": "<=",
}


def _filter_to_cypher(
    prop_filter: Dict[str, Any],
    params: Dict[str, Any],
) -> str:
    """Tradueix un filtre MongoDB-style a una clàusula Cypher WHERE.

    Suporta operadors escalars ($eq $ne $gt $gte $lt $lte), llistes
    ($in $nin) i $exists.  Operadors no traduïbles ($regex, $contains,
    $or, $and, $not) s'ometen del WHERE de Cypher i s'apliquen
    posteriorment en memòria via _nx_match.

    Args:
        prop_filter: Filtre de propietats, p.ex.
            ``{"score": {"$lte": 0.8}, "status": "ok"}``.
        params: Dict de paràmetres Cypher; s'omple durant la crida.

    Returns:
        Fragment WHERE sense la paraula clau "WHERE", o cadena buida.
    """
    parts: List[str] = []
    for field, cond in prop_filter.items():
        if field.startswith("$"):
            # Combinadors lògics de nivell superior: no es tradueixen
            continue
        pbase = f"_f{len(params)}"
        if isinstance(cond, dict):
            for op, val in cond.items():
                p = f"{pbase}_{op[1:]}"
                if op in _OP_TO_CYPHER:
                    parts.append(f"n.{field} {_OP_TO_CYPHER[op]} ${p}")
                    params[p] = val
                elif op == "$in":
                    parts.append(f"n.{field} IN ${p}")
                    params[p] = list(val)
                elif op == "$nin":
                    parts.append(f"NOT n.{field} IN ${p}")
                    params[p] = list(val)
                elif op == "$exists":
                    parts.append(
                        f"n.{field} IS NOT NULL"
                        if val else f"n.{field} IS NULL"
                    )
                # $regex, $contains → filtre en memòria, no al WHERE
        elif isinstance(cond, list):
            p = f"{pbase}_in"
            parts.append(f"n.{field} IN ${p}")
            params[p] = cond
        else:
            # Igualtat directa
            p = pbase
            parts.append(f"n.{field} = ${p}")
            params[p] = cond
    return " AND ".join(parts)


# ---------------------------------------------------------------------------
# GraphDataset
# ---------------------------------------------------------------------------

class GraphDataset(IterableDataset):
    """Stream nodes lazily from a :class:`~cvcdocdb.graph_store.GraphStore`.

    Each item is a plain ``dict`` with at least:

    * ``node_id``    – internal store id (``int``)
    * ``main_label`` – primary label (``str``)
    * ``pk``         – primary-key dict
    * … every other stored attribute

    Multi-worker :class:`DataLoader` is supported automatically: the
    node population is sharded across workers with **no copies** — just
    a sliced iterator.

    Args:
        store: A live ``NetworkXGraph`` or ``Neo4jGraph`` instance.
        label_filter: Keep only nodes whose ``main_label`` is in this
            set.  A bare string is accepted as a singleton.  ``None``
            means no restriction.
        property_filter: MongoDB-style filter dict.  Supports:

            * **igualtat directa** – ``{"status": "ok"}``
            * **operadors escalars** – ``{"score": {"$lte": 0.8}}``

              ``$eq`` ``$ne`` ``$gt`` ``$gte`` ``$lt`` ``$lte``
            * **pertinença** – ``{"tag": {"$in": ["a", "b"]}}``

              ``$in`` ``$nin``
            * **existència** – ``{"bbox": {"$exists": True}}``
            * **regex / conté** – ``{"name": {"$regex": "^Doc"}}``
            * **combinadors** – ``{"$and": [...]}`` ``{"$or": [...]}``
              ``{"$not": {...}}``

              A Neo4j, només la igualtat directa i els operadors
              anteriors (excepte $regex/$contains i els combinadors) es
              tradueixen a la clàusula ``WHERE`` de Cypher — la resta
              del filtre s'aplica igualment, com a comprovació final en
              memòria sobre cada fila retornada, així que el
              comportament és idèntic al de NetworkX; l'única diferència
              és que a Neo4j part del filtratge no es pot empènyer a la
              base de dades.

        transform: Optional callable applied to each sample dict before
            yielding.  May return any type.
        chunk_size: Rows fetched per Cypher round-trip (Neo4j only).
    """

    def __init__(
        self,
        store: GraphStore,
        label_filter: Optional[Union[str, List[str]]] = None,
        property_filter: Optional[Dict[str, Any]] = None,
        transform: Optional[Callable[[Dict[str, Any]], Any]] = None,
        chunk_size: int = 256,
    ) -> None:
        super().__init__()
        self._store = store
        self._labels: Optional[FrozenSet[str]] = (
            frozenset([label_filter]) if isinstance(label_filter, str)
            else frozenset(label_filter) if label_filter is not None
            else None
        )
        self._props: Dict[str, Any] = property_filter or {}
        self._transform = transform
        self._chunk_size = chunk_size

    # ------------------------------------------------------------------
    # IterableDataset protocol
    # ------------------------------------------------------------------

    def __iter__(self) -> Iterator[Any]:
        wi = torch.utils.data.get_worker_info()
        backend = type(self._store).__name__
        it = (
            self._iter_networkx(wi) if backend == "NetworkXGraph"
            else self._iter_neo4j(wi)
        )
        if self._transform is None:
            yield from it
        else:
            for sample in it:
                yield self._transform(sample)

    # ------------------------------------------------------------------
    # Backend iterators
    # ------------------------------------------------------------------

    def _iter_networkx(self, wi: Any) -> Iterator[Dict[str, Any]]:
        """Zero-copy iteration over NetworkXGraph._node_attrs."""
        items = self._store._node_attrs.items()  # dict_items view — no copy

        if wi is not None:
            n = len(self._store._node_attrs)
            per = math.ceil(n / wi.num_workers)
            start = wi.id * per
            items = itertools.islice(items, start, start + per)

        labels, props = self._labels, self._props
        for nid, attrs in items:
            if labels is not None and attrs.get("main_label") not in labels:
                continue
            if props and not _nx_match(attrs, props):
                continue
            yield {
                "node_id": nid,
                "main_label": attrs.get("main_label", ""),
                "pk": attrs.get("pk", {}),
                **{k: v for k, v in attrs.items()
                   if k not in ("main_label", "pk", "labels")},
            }

    def _iter_neo4j(self, wi: Any) -> Iterator[Dict[str, Any]]:
        """Cursor-based SKIP/LIMIT pagination — one chunk per round-trip.

        ``ORDER BY id(n)`` keeps the SKIP/LIMIT windows stable across
        round-trips (and across the disjoint windows handed to different
        DataLoader workers) — without it Neo4j does not guarantee row
        order, so a node could be yielded twice or skipped entirely.

        The Cypher WHERE clause built by ``_filter_to_cypher`` only
        covers what translates directly (equality, scalar/`` $in``/``
        $exists`` operators); combinators (``$or``/``$and``/``$not``) and
        ``$regex``/``$contains`` are silently omitted from it. ``_nx_match``
        is applied to every fetched row as a correctness fallback so the
        *full* property_filter is always honoured, mirroring
        ``_iter_networkx``.
        """
        label_part, where_part, params = self._build_cypher_clauses()

        total_res = self._store.query(
            f"MATCH (n{label_part}) {where_part} RETURN count(n) AS total",
            params=dict(params),
        )
        total: int = total_res[0].get("total", 0) if total_res else 0
        if total == 0:
            return

        skip, end = (0, total) if wi is None else _worker_slice(total, wi)
        offset = skip
        props = self._props
        while offset < end:
            chunk = min(self._chunk_size, end - offset)
            rows = self._store.query(
                f"MATCH (n{label_part}) {where_part} "
                f"RETURN id(n) AS nid, n AS n "
                f"ORDER BY id(n) SKIP {offset} LIMIT {chunk}",
                params=dict(params),
            )
            if not rows:
                break
            for row in rows:
                sample = _neo4j_to_sample(row.get("n"), row.get("nid"))
                if props and not _nx_match(sample, props):
                    continue
                yield sample
            offset += chunk

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_cypher_clauses(self):
        label_part = (":" + "|".join(self._labels)) if self._labels else ""
        params: Dict[str, Any] = {}
        where_body = _filter_to_cypher(self._props, params)
        where_part = ("WHERE " + where_body) if where_body else ""
        return label_part, where_part, params


# ---------------------------------------------------------------------------
# GraphDataLoader
# ---------------------------------------------------------------------------

class GraphDataLoader(DataLoader):
    """DataLoader pre-configured for :class:`GraphDataset`.

    Uses :func:`graph_collate_fn` by default (stacks numerics to
    tensors, keeps strings as lists).  All :class:`DataLoader` kwargs
    are forwarded unchanged.
    """

    def __init__(self, dataset, batch_size=32, num_workers=0,
                 collate_fn=None, **kwargs):
        super().__init__(
            dataset, batch_size=batch_size, num_workers=num_workers,
            collate_fn=collate_fn or graph_collate_fn, **kwargs,
        )


# ---------------------------------------------------------------------------
# Collate
# ---------------------------------------------------------------------------

def graph_collate_fn(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Collate node dicts: stack numerics → Tensor, keep the rest as list.

    Gestiona automàticament:

    * Escalars ``int`` / ``float`` → ``torch.tensor([...])``
    * Tensors ``torch.Tensor``     → ``torch.stack([...])``
    * Llistes numèriques o ``np.ndarray`` (embeddings) → ``torch.tensor([...])``
    * La resta                     → ``list`` sense canvis
    """
    if not samples:
        return {}
    try:
        import numpy as _np
        _NUMPY = _np
    except ImportError:
        _NUMPY = None

    batch: Dict[str, Any] = {}
    for key in samples[0]:
        vals = [s[key] for s in samples]
        first = vals[0]
        if isinstance(first, (int, float)):
            try:
                batch[key] = torch.tensor(vals)
                continue
            except (TypeError, ValueError):
                pass
        elif isinstance(first, torch.Tensor):
            try:
                batch[key] = torch.stack(vals)
                continue
            except RuntimeError:
                pass
        elif isinstance(first, (list, tuple)):
            # Embedding o vector: llista de llistes → Tensor [B, D]
            try:
                batch[key] = torch.tensor(vals, dtype=torch.float32)
                continue
            except (TypeError, ValueError, RuntimeError):
                pass
        elif _NUMPY is not None and isinstance(first, _NUMPY.ndarray):
            try:
                batch[key] = torch.from_numpy(_NUMPY.stack(vals))
                continue
            except (TypeError, ValueError):
                pass
        batch[key] = vals
    return batch


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------


def _build_x(
    scalar_rows: List[List[float]],
    vector_rows: List[Any],
    missing: float = 0.0,
) -> "torch.Tensor":
    """Construeix la matriu de característiques ``x`` combinant escalars i vectors.

    Args:
        scalar_rows: Llista de llistes d'escalars (una per node).
            Pot ser buida si no hi ha atributs escalars.
        vector_rows: Llista d'embeddings (llista, tuple o np.ndarray).
            Pot ser buida si no hi ha ``vector_attr``.
        missing: Valor per omplir dimensions mancants.

    Returns:
        FloatTensor de forma ``[N, D_scalars + D_vector]``.
        Si tots dos estan buits retorna ``zeros([N, 0])``.
    """
    import numpy as _np

    n = max(len(scalar_rows), len(vector_rows))
    if n == 0:
        return torch.zeros((0, 0))

    parts: List["torch.Tensor"] = []

    if scalar_rows and scalar_rows[0]:
        parts.append(torch.tensor(scalar_rows, dtype=torch.float32))

    if vector_rows:
        # Normalitzem: llista/tuple/ndarray → ndarray float32
        arrays = []
        ref_dim: Optional[int] = None
        for v in vector_rows:
            if v is None:
                arrays.append(None)
                continue
            arr = _np.asarray(v, dtype=_np.float32)
            if arr.ndim != 1:
                arrays.append(None)
                continue
            if ref_dim is None:
                ref_dim = arr.shape[0]
            arrays.append(arr)

        if ref_dim is None:
            ref_dim = 0

        filled = [
            arr if (arr is not None and arr.shape[0] == ref_dim)
            else _np.full(ref_dim, missing, dtype=_np.float32)
            for arr in arrays
        ]
        parts.append(torch.from_numpy(_np.stack(filled)))

    if not parts:
        return torch.zeros((n, 0))
    if len(parts) == 1:
        return parts[0]
    return torch.cat(parts, dim=1)


def _worker_slice(total: int, wi: Any):
    per = math.ceil(total / wi.num_workers)
    start = wi.id * per
    return start, min(start + per, total)


def _neo4j_to_sample(val: Any, node_id: Optional[int] = None) -> Dict[str, Any]:
    if isinstance(val, dict):
        labels = val.get("labels", [])
        props = val.get("properties", val)
        return {
            "node_id": node_id,
            "main_label": labels[0] if labels else props.get("main_label", ""),
            "pk": props.get("pk", {}),
            **{k: v for k, v in props.items() if k != "pk"},
        }
    return {"node_id": node_id, "main_label": "", "pk": {}, "raw": str(val)}


# ===========================================================================
# PyTorch Geometric support
# ===========================================================================
#
# Les funcions següents requereixen ``torch_geometric`` però no l'importen
# fins que es criden — el mòdul és importable sense PyG instal·lat.
#
# Ús ràpid::
#
#     from cvcdocdb.torch_dataloader import to_pyg_data, SubgraphDataset
#
#     # Graf sencer → Data (node classification)
#     data = to_pyg_data(store, node_attrs=["width", "height"], label_attr="class_id")
#     # data.x          → FloatTensor [N, 2]
#     # data.edge_index → LongTensor  [2, E]
#     # data.y          → LongTensor  [N]
#
#     # Stream de subgrafs → DataLoader de PyG (graph / link prediction)
#     ds     = SubgraphDataset(store, hops=2, node_attrs=["w", "h"])
#     loader = PyGDataLoader(ds, batch_size=16)
#     for batch in loader:          # torch_geometric.data.Batch
#         out = model(batch.x, batch.edge_index, batch.batch)


def to_pyg_data(
    store: GraphStore,
    node_attrs: Optional[List[str]] = None,
    label_attr: Optional[str] = None,
    vector_attr: Optional[str] = None,
    missing: float = 0.0,
) -> "torch_geometric.data.Data":
    """Converteix tot el graf a un únic objecte PyG ``Data``.

    Adequat per a **node classification** sobre el graf complet.
    Els ids interns del store es remapegen a índexs locals 0…N-1.

    Args:
        store: Backend connectat (``NetworkXGraph`` o ``Neo4jGraph``).
        node_attrs: Atributs escalars numèrics que formen part de ``x``
            (un valor per atribut per node).  ``None`` → no s'afegeixen
            escalars.
        label_attr: Atribut per a ``y`` (enter).
            ``None`` → ``data.y`` no s'afegeix.
        vector_attr: Nom de l'atribut que conté l'embedding del node
            (llista de floats o ``np.ndarray``).  Si es dona, els
            vectors es concatenen amb els escalars de *node_attrs* per
            formar ``x``.  ``None`` → no s'usa cap embedding.
        missing: Valor per a atributs absents o no numèrics.

    Returns:
        ``torch_geometric.data.Data`` amb:

        * ``x``          – FloatTensor ``[N, len(node_attrs)]``
        * ``edge_index`` – LongTensor ``[2, E]`` (índexs locals)
        * ``y``          – LongTensor ``[N]`` (si *label_attr* és donat)
        * ``node_ids``   – LongTensor ``[N]`` amb els ids originals del store

    Example::

        data = to_pyg_data(store, node_attrs=["width", "height"],
                           label_attr="class_id")
        # Entrenar GCN estàndard:
        out = model(data.x, data.edge_index)
        loss = F.cross_entropy(out[train_mask], data.y[train_mask])
    """
    try:
        from torch_geometric.data import Data
    except ImportError as exc:
        raise ImportError(
            "torch_geometric és necessari.  Instal·la amb:  pip install torch_geometric"
        ) from exc

    backend = type(store).__name__

    # ── Recollir nodes ──────────────────────────────────────────────────
    if backend == "NetworkXGraph":
        items = list(store._node_attrs.items())   # [(nid, attrs), …]
    else:
        rows = store.query("MATCH (n) RETURN id(n) AS nid, properties(n) AS props")
        items = [(r["nid"], r.get("props") or {}) for r in rows]

    if not items:
        from torch_geometric.data import Data as _Data
        return _Data(
            x=torch.zeros((0, len(node_attrs or []))),
            edge_index=torch.zeros((2, 0), dtype=torch.long),
        )

    orig_ids = [nid for nid, _ in items]
    local_idx: Dict[int, int] = {nid: i for i, nid in enumerate(orig_ids)}

    # Matriu de característiques
    node_attrs = node_attrs or []
    scalar_rows: List[List[float]] = []
    vector_rows: List[Any] = []
    labels: List[int] = []
    for _, attrs in items:
        row = []
        for key in node_attrs:
            val = attrs.get(key, missing)
            try:
                row.append(float(val))
            except (TypeError, ValueError):
                row.append(missing)
        scalar_rows.append(row)
        if vector_attr is not None:
            vec = attrs.get(vector_attr)
            vector_rows.append(vec)
        if label_attr is not None:
            lv = attrs.get(label_attr, 0)
            try:
                labels.append(int(lv))
            except (TypeError, ValueError):
                labels.append(0)

    x = _build_x(scalar_rows, vector_rows if vector_attr else [], missing)

    # ── Recollir arestes ────────────────────────────────────────────────
    if backend == "NetworkXGraph":
        raw_edges = store.get_edges()   # [(src_id, dst_id, rel_type), …]
    else:
        raw_edges = [
            (r["src"], r["dst"], r.get("rel_type", ""))
            for r in store.query(
                "MATCH (a)-[r]->(b) RETURN id(a) AS src, id(b) AS dst, type(r) AS rel_type"
            )
        ]

    src_list, dst_list = [], []
    for s, d, _ in raw_edges:
        if s in local_idx and d in local_idx:
            src_list.append(local_idx[s])
            dst_list.append(local_idx[d])

    edge_index = torch.tensor([src_list, dst_list], dtype=torch.long)

    data = Data(
        x=x,
        edge_index=edge_index,
        node_ids=torch.tensor(orig_ids, dtype=torch.long),
    )
    if label_attr is not None:
        data.y = torch.tensor(labels, dtype=torch.long)
    return data


class SubgraphDataset(IterableDataset):
    """Stream de subgrafs ego (k-hop) com a objectes PyG ``Data``.

    Cada seed node definit per *label_filter* / *property_filter* genera
    un ``Data`` que conté el seu k-hop neighbourhood.  Adequat per a
    **graph classification** i **link prediction** amb PyG.

    Requereix ``torch_geometric`` en temps d'execució.

    Args:
        store: Backend connectat.
        hops: Profunditat del BFS al voltant de cada seed.
        node_attrs: Atributs numèrics per construir ``x``.
        label_attr: Atribut per a ``y`` (nivell de subgraf).
        label_filter: Filtre de labels per als seeds.
        property_filter: Filtre de propietats per als seeds.
        missing: Valor per a atributs absents.
        chunk_size: Mida del chunk per a Neo4j.

    Example::

        ds     = SubgraphDataset(store, hops=2,
                                 node_attrs=["width", "height"],
                                 label_filter="Page")
        loader = PyGDataLoader(ds, batch_size=16)
        for batch in loader:
            out = model(batch.x, batch.edge_index, batch.batch)
    """

    def __init__(
        self,
        store: GraphStore,
        hops: int = 1,
        node_attrs: Optional[List[str]] = None,
        label_attr: Optional[str] = None,
        vector_attr: Optional[str] = None,
        label_filter: Optional[Union[str, List[str]]] = None,
        property_filter: Optional[Dict[str, Any]] = None,
        missing: float = 0.0,
        chunk_size: int = 256,
    ) -> None:
        super().__init__()
        self._seed_ds = GraphDataset(
            store, label_filter=label_filter,
            property_filter=property_filter, chunk_size=chunk_size,
        )
        self._store = store
        self._hops = hops
        self._node_attrs: List[str] = node_attrs or []
        self._vector_attr = vector_attr
        self._label_attr = label_attr
        self._missing = missing

    def __iter__(self) -> Iterator["torch_geometric.data.Data"]:
        try:
            from torch_geometric.data import Data
        except ImportError as exc:
            raise ImportError(
                "torch_geometric és necessari.  Instal·la amb:  pip install torch_geometric"
            ) from exc

        backend = type(self._store).__name__
        expand = (self._expand_nx if backend == "NetworkXGraph"
                  else self._expand_neo4j)

        for seed in self._seed_ds:
            seed_id: int = seed["node_id"]

            # BFS: recollim veïns i arestes en un sol pas.
            # adjacency: nid -> list[nb] (descoberts fins ara)
            visited: List[int] = [seed_id]
            visited_set: set = {seed_id}
            adjacency: Dict[int, List[int]] = {seed_id: []}
            frontier = [seed_id]
            for _ in range(self._hops):
                nxt: List[int] = []
                for nid in frontier:
                    for nb in expand(nid):
                        adjacency[nid].append(nb)
                        if nb not in visited_set:
                            visited_set.add(nb)
                            visited.append(nb)
                            adjacency[nb] = []
                            nxt.append(nb)
                frontier = nxt

            local_idx = {nid: i for i, nid in enumerate(visited)}

            # Atributs: una sola query batch per Neo4j (elimina N+1)
            if backend == "NetworkXGraph":
                all_attrs = {nid: self._store._node_attrs.get(nid, {})
                             for nid in visited}
            else:
                all_attrs = self._fetch_neo4j_attrs_batch(visited)

            # Node features
            scalar_rows: List[List[float]] = []
            vector_rows: List[Any] = []
            labels: List[int] = []
            for nid in visited:
                attrs = all_attrs.get(nid, {})
                row: List[float] = []
                for key in self._node_attrs:
                    val = attrs.get(key, self._missing)
                    try:
                        row.append(float(val))
                    except (TypeError, ValueError):
                        row.append(self._missing)
                scalar_rows.append(row)
                if self._vector_attr is not None:
                    vector_rows.append(attrs.get(self._vector_attr))
                if self._label_attr is not None:
                    lv = attrs.get(self._label_attr, 0)
                    try:
                        labels.append(int(lv))
                    except (TypeError, ValueError):
                        labels.append(0)

            x = _build_x(
                scalar_rows,
                vector_rows if self._vector_attr else [],
                self._missing,
            )

            # Edge index: reutilitzem adjacency ja construïda al BFS
            src_list: List[int] = []
            dst_list: List[int] = []
            for nid, nbs in adjacency.items():
                li = local_idx[nid]
                for nb in nbs:
                    if nb in local_idx:
                        src_list.append(li)
                        dst_list.append(local_idx[nb])

            edge_index = torch.tensor([src_list, dst_list], dtype=torch.long)

            data = Data(
                x=x,
                edge_index=edge_index,
                node_ids=torch.tensor(visited, dtype=torch.long),
            )
            if self._label_attr is not None:
                data.y = torch.tensor(labels, dtype=torch.long)
            # Embedding accessible per separat a data.emb (a més d'estar fusionat a data.x)
            if self._vector_attr and vector_rows:
                emb_tensor = _build_x([], vector_rows, self._missing)
                if emb_tensor.numel() > 0:
                    data.emb = emb_tensor
            yield data

    def _expand_nx(self, node_id: int) -> List[int]:
        g = self._store._graph
        return list(g.successors(node_id)) + list(g.predecessors(node_id))

    def _expand_neo4j(self, node_id: int) -> List[int]:
        res = self._store.query(
            "MATCH (n)--(m) WHERE id(n) = $nid RETURN DISTINCT id(m) AS mid",
            params={"nid": node_id},
        )
        return [r["mid"] for r in res if r.get("mid") is not None]

    def _fetch_neo4j_attrs_batch(self, node_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Una sola query per tots els nodes del subgraf (elimina N+1)."""
        res = self._store.query(
            "MATCH (n) WHERE id(n) IN $ids RETURN id(n) AS nid, properties(n) AS props",
            params={"ids": node_ids},
        )
        return {r["nid"]: (r.get("props") or {}) for r in res}


def PyGDataLoader(
    dataset: IterableDataset,
    batch_size: int = 32,
    **kwargs: Any,
) -> "torch_geometric.loader.DataLoader":
    """DataLoader de PyG pre-configurat per a :class:`SubgraphDataset`.

    Args:
        dataset: Un :class:`SubgraphDataset` (o qualsevol dataset que
            retorni objectes ``Data``).
        batch_size: Nombre de subgrafs per batch.
        **kwargs: Argumentes addicionals per a
            ``torch_geometric.loader.DataLoader``.

    Returns:
        ``torch_geometric.loader.DataLoader`` que emet objectes
        ``torch_geometric.data.Batch``.
    """
    try:
        from torch_geometric.loader import DataLoader as _PYGLoader
    except ImportError as exc:
        raise ImportError(
            "torch_geometric és necessari.  Instal·la amb:  pip install torch_geometric"
        ) from exc
    return _PYGLoader(dataset, batch_size=batch_size, **kwargs)


def to_hetero_edge_index_dict(
    store: GraphStore,
    node_type_attr: str = "main_label",
    chunk_size: int = 1024,
) -> "tuple[Dict[tuple, torch.Tensor], Dict[str, int], Dict[str, Dict[int, int]]]":
    """Carrega la topologia heterogènia *completa* del graf, de la manera
    més lleugera possible: una sola passada pels edges, sense carregar cap
    atribut de node (només el tipus), pensada per a algorismes que
    necessiten tot el graf (p.ex. :class:`torch_geometric.nn.MetaPath2Vec`)
    en lloc de batches per node com :class:`GraphDataset`/:class:`SubgraphDataset`.

    Nodes sense cap edge queden fora: sense veïns no hi ha res a aprendre'n
    via random walks, i incloure'ls no aportaria res al cost d'una consulta
    addicional.

    Args:
        store: Backend connectat (``NetworkXGraph`` o ``Neo4jGraph``).
        node_type_attr: Atribut que determina el tipus de node
            (per defecte ``main_label``).
        chunk_size: Mida de lot per a les consultes de tipus a Neo4j
            (elimina el N+1).

    Returns:
        Tuple ``(edge_index_dict, num_nodes_dict, node_maps)``:

        * ``edge_index_dict``: ``{(src_type, rel_type, dst_type): LongTensor[2, E]}``
        * ``num_nodes_dict``: ``{node_type: nombre de nodes}``
        * ``node_maps``: ``{node_type: {id_original: índex_local}}`` — per
          recuperar els ids originals del store a partir dels índexs
          locals usats a ``edge_index_dict``.

    Example::

        edge_index_dict, num_nodes_dict, node_maps = to_hetero_edge_index_dict(store)
        model = MetaPath2Vec(edge_index_dict, embedding_dim=16,
                              metapath=[("author", "AUTHORED", "paper"), ...],
                              walk_length=4, context_size=2,
                              num_nodes_dict=num_nodes_dict)
    """
    backend = type(store).__name__

    if backend == "NetworkXGraph":
        edges = store.get_edges()

        def fetch_types(node_ids: List[int]) -> Dict[int, Optional[str]]:
            return {
                nid: (store._node_attrs.get(nid) or {}).get(node_type_attr)
                for nid in node_ids
            }
    else:
        edges = [
            (r["src"], r["dst"], r["rel"])
            for r in store.query(
                "MATCH (n)-[r]->(m) RETURN id(n) AS src, id(m) AS dst, type(r) AS rel"
            )
        ]

        def fetch_types(node_ids: List[int]) -> Dict[int, Optional[str]]:
            types: Dict[int, Optional[str]] = {}
            for i in range(0, len(node_ids), chunk_size):
                chunk = node_ids[i:i + chunk_size]
                res = store.query(
                    "MATCH (n) WHERE id(n) IN $ids "
                    "RETURN id(n) AS nid, properties(n) AS props",
                    params={"ids": chunk},
                )
                for r in res:
                    types[r["nid"]] = (r.get("props") or {}).get(node_type_attr)
            return types

    referenced_ids = sorted({nid for u, v, _ in edges for nid in (u, v)})
    type_of = fetch_types(referenced_ids)

    node_maps: Dict[str, Dict[int, int]] = {}

    def local_idx(nid: int, ntype: str) -> int:
        idx_map = node_maps.setdefault(ntype, {})
        idx = idx_map.get(nid)
        if idx is None:
            idx = len(idx_map)
            idx_map[nid] = idx
        return idx

    edge_lists: Dict[tuple, tuple] = {}
    for u, v, rel in edges:
        ut, vt = type_of.get(u), type_of.get(v)
        if not ut or not vt:
            continue
        key = (ut, rel, vt)
        src_list, dst_list = edge_lists.setdefault(key, ([], []))
        src_list.append(local_idx(u, ut))
        dst_list.append(local_idx(v, vt))

    edge_index_dict = {
        key: torch.tensor([src, dst], dtype=torch.long)
        for key, (src, dst) in edge_lists.items()
    }
    num_nodes_dict = {ntype: len(m) for ntype, m in node_maps.items()}
    return edge_index_dict, num_nodes_dict, node_maps


def split_edges_by_node_property(
    edge_index: "torch.Tensor",
    node_values: "torch.Tensor",
    **conditions: Callable[["torch.Tensor", "torch.Tensor"], "torch.Tensor"],
) -> "Dict[str, torch.Tensor]":
    """Split ``edge_index`` into named edge subsets (train/val/test/...) by
    a per-node property.

    Useful for a temporal/ordinal split — e.g. a chain ``A(num=1) -
    B(num=2) - C(num=3) - D(num=4)``, training on edges where both
    endpoints are "early", validating in the middle, and testing on edges
    where both are "late". A node such as ``B`` naturally sits on a
    boundary and can appear in edges on both sides of it: that's expected
    in the usual **transductive** link-prediction setting (every node
    stays visible; only edges are withheld) — what this function guards
    against is the same *edge* ending up in more than one split.

    Args:
        edge_index: ``LongTensor [2, E]`` with local node indices (as
            returned by :func:`to_pyg_data` / :func:`to_hetero_edge_index_dict`).
        node_values: ``Tensor [N]`` — one property value per node, indexed
            by the same local indices used in ``edge_index``. Must be
            numeric — encode a categorical/string property to integer
            codes first (see the second example below).
        **conditions: two or more named splits, each
            ``(src_values, dst_values) -> BoolTensor [E]`` — e.g.
            ``train=lambda s, d: (s <= 2) & (d <= 2)``. The keyword name
            becomes the key in the returned dict.

    Returns:
        ``{name: edge_index_subset}`` — one ``LongTensor [2, E']`` per
        condition, in the same order they were passed. An edge matching
        none of the conditions is simply dropped from every split.

    Raises:
        ValueError: If fewer than two conditions are given, or if two
            conditions both match the same edge — define non-overlapping
            conditions instead (e.g. a ``max(...) <= k`` / ``min(...) >= k``
            pair, as in the example below, rather than independent
            ``<=``/``>=`` checks on each endpoint separately).

    Example (train/test, numeric/ordinal property)::

        edge_index, num_nodes, node_maps = to_hetero_edge_index_dict(store)[...]
        num = torch.tensor([attrs["num"] for ...])  # aligned with node_maps
        splits = split_edges_by_node_property(
            edge_index, num,
            train=lambda s, d: torch.maximum(s, d) <= 2,
            test=lambda s, d: torch.minimum(s, d) >= 2,
        )
        train_ei, test_ei = splits["train"], splits["test"]

    Example (train/val/test, three-way)::

        splits = split_edges_by_node_property(
            edge_index, num,
            train=lambda s, d: torch.maximum(s, d) <= 2,
            val=lambda s, d: (torch.minimum(s, d) >= 2) & (torch.maximum(s, d) <= 3),
            test=lambda s, d: torch.minimum(s, d) >= 3,
        )

    Example (categorical property, e.g. ``pais``)::

        # Codify the category to an int per node first — the function
        # itself only ever sees numbers.
        countries = [attrs["pais"] for ...]  # aligned with node_maps
        code_of = {c: i for i, c in enumerate(sorted(set(countries)))}
        codes = torch.tensor([code_of[c] for c in countries])

        es = code_of["ES"]
        splits = split_edges_by_node_property(
            edge_index, codes,
            # Train on edges entirely inside "ES", test on everything
            # that crosses to or lies fully outside it.
            train=lambda s, d: (s == es) & (d == es),
            test=lambda s, d: (s != es) | (d != es),
        )
    """
    if len(conditions) < 2:
        raise ValueError(
            "split_edges_by_node_property needs at least two named "
            "conditions (e.g. train=..., test=...)."
        )
    src, dst = edge_index[0], edge_index[1]
    src_val, dst_val = node_values[src], node_values[dst]
    masks = {name: cond(src_val, dst_val) for name, cond in conditions.items()}

    names = list(masks)
    for i, name_a in enumerate(names):
        for name_b in names[i + 1:]:
            if bool((masks[name_a] & masks[name_b]).any()):
                raise ValueError(
                    f"'{name_a}' and '{name_b}' overlap for at least one "
                    "edge — define non-overlapping conditions (e.g. "
                    "max(src, dst) <= k for one split, min(src, dst) >= k "
                    "for the next)."
                )

    return {name: edge_index[:, mask] for name, mask in masks.items()}


_EDGE_EMBEDDING_OPS = ("hadamard", "concat", "average", "l1", "l2", "dot")


def edge_embeddings(
    node_embeddings: "torch.Tensor",
    edge_index: "torch.Tensor",
    op: str = "hadamard",
) -> "torch.Tensor":
    """Combine per-node embeddings into a per-edge representation.

    Node embedding methods (:class:`~torch_geometric.nn.MetaPath2Vec`,
    Node2Vec, GNN encoders, ...) only ever produce one vector per *node* —
    there is no such thing as a learned "edge embedding". To score or
    classify an edge (typical for link prediction) you combine its two
    endpoints' embeddings with one of the operators below.

    Args:
        node_embeddings: ``FloatTensor [N, D]``.
        edge_index: ``LongTensor [2, E]`` with local indices into
            ``node_embeddings`` (e.g. one value of the dict returned by
            :func:`split_edges_by_node_property`).
        op: One of:

            * ``"hadamard"`` (default) — element-wise product, ``[E, D]``.
            * ``"concat"`` — ``[src; dst]`` concatenation, ``[E, 2*D]``.
            * ``"average"`` — ``(src + dst) / 2``, ``[E, D]``.
            * ``"l1"`` — ``|src - dst|``, ``[E, D]``.
            * ``"l2"`` — ``(src - dst) ** 2``, ``[E, D]``.
            * ``"dot"`` — dot-product similarity score, ``[E]`` (1-D).
              Feed this through a sigmoid for a link-existence probability.

    Returns:
        A tensor whose shape depends on ``op`` (see above).

    Raises:
        ValueError: If ``op`` is not one of the supported operators.

    Example (link-prediction score)::

        splits = split_edges_by_node_property(edge_index, num, train=..., test=...)
        train_scores = edge_embeddings(paper_embeddings, splits["train"], op="dot")
        probs = torch.sigmoid(train_scores)  # existence probability per edge
    """
    if op not in _EDGE_EMBEDDING_OPS:
        raise ValueError(
            f"Unknown op {op!r}. Expected one of: {', '.join(_EDGE_EMBEDDING_OPS)}."
        )
    src, dst = edge_index[0], edge_index[1]
    e_src, e_dst = node_embeddings[src], node_embeddings[dst]
    if op == "hadamard":
        return e_src * e_dst
    if op == "concat":
        return torch.cat([e_src, e_dst], dim=-1)
    if op == "average":
        return (e_src + e_dst) / 2
    if op == "l1":
        return (e_src - e_dst).abs()
    if op == "l2":
        return (e_src - e_dst) ** 2
    return (e_src * e_dst).sum(dim=-1)  # "dot"
