"""Tests for cvcdocdb.torch_dataloader.

Run::

    pytest test/test_torch_dataloader.py -v -m integration
"""

from __future__ import annotations

import itertools
import math
from unittest.mock import MagicMock

import pytest

try:
    import torch
    from torch.utils.data import DataLoader
except ImportError:
    pytest.skip("PyTorch not installed", allow_module_level=True)

from cvcdocdb import NetworkXGraph, Node, Relation
from cvcdocdb.torch_dataloader import (
    GraphDataLoader,
    GraphDataset,
    _worker_slice,
    edge_embeddings,
    graph_collate_fn,
    split_edges_by_node_property,
    to_hetero_edge_index_dict,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def empty_store(tmp_path):
    return NetworkXGraph(str(tmp_path / "g.pkl"))


@pytest.fixture()
def store(tmp_path):
    """5 Document + 3 Page nodes."""
    s = NetworkXGraph(str(tmp_path / "g.pkl"))
    for i in range(5):
        s.insertNode(Node(pk={"id": i}, main_label="Document",
                          score=float(i), status="ok"))
    for i in range(3):
        parent = Node(pk={"id": i}, main_label="Document")
        s.insertNode(Node(pk={"page": i}, main_label="Page",
                          parent=parent, parent_relation="HAS"))
    s.close()
    return NetworkXGraph(str(tmp_path / "g.pkl"))


# ---------------------------------------------------------------------------
# Operator filters (via _nx_match)
# ---------------------------------------------------------------------------

class TestOperatorFilters:
    def test_exact_equality(self, store):
        samples = list(GraphDataset(store, property_filter={"status": "ok"}))
        assert len(samples) == 5

    def test_lte(self, store):
        samples = list(GraphDataset(store, label_filter="Document",
                                    property_filter={"score": {"$lte": 2.0}}))
        # scores 0.0, 1.0, 2.0 → 3 nodes
        assert len(samples) == 3
        assert all(s["score"] <= 2.0 for s in samples)

    def test_lt(self, store):
        samples = list(GraphDataset(store, label_filter="Document",
                                    property_filter={"score": {"$lt": 2.0}}))
        assert len(samples) == 2
        assert all(s["score"] < 2.0 for s in samples)

    def test_gt(self, store):
        samples = list(GraphDataset(store, label_filter="Document",
                                    property_filter={"score": {"$gt": 3.0}}))
        assert len(samples) == 1
        assert samples[0]["score"] == 4.0

    def test_gte(self, store):
        samples = list(GraphDataset(store, label_filter="Document",
                                    property_filter={"score": {"$gte": 3.0}}))
        assert len(samples) == 2
        assert all(s["score"] >= 3.0 for s in samples)

    def test_ne(self, store):
        samples = list(GraphDataset(store, label_filter="Document",
                                    property_filter={"score": {"$ne": 0.0}}))
        assert len(samples) == 4
        assert all(s["score"] != 0.0 for s in samples)

    def test_in(self, store):
        samples = list(GraphDataset(store, label_filter="Document",
                                    property_filter={"score": {"$in": [1.0, 3.0]}}))
        assert len(samples) == 2
        assert {s["score"] for s in samples} == {1.0, 3.0}

    def test_nin(self, store):
        samples = list(GraphDataset(store, label_filter="Document",
                                    property_filter={"score": {"$nin": [0.0, 1.0, 2.0]}}))
        assert len(samples) == 2
        assert all(s["score"] not in (0.0, 1.0, 2.0) for s in samples)

    def test_exists_true(self, store):
        samples = list(GraphDataset(store, property_filter={"status": {"$exists": True}}))
        assert len(samples) == 5  # only Document nodes have status

    def test_exists_false(self, store):
        samples = list(GraphDataset(store, label_filter="Document",
                                    property_filter={"status": {"$exists": False}}))
        assert len(samples) == 0

    def test_combined_operators(self, store):
        # score >= 1 AND score <= 3
        samples = list(GraphDataset(store, label_filter="Document",
                                    property_filter={
                                        "score": {"$gte": 1.0, "$lte": 3.0}
                                    }))
        assert len(samples) == 3
        assert all(1.0 <= s["score"] <= 3.0 for s in samples)

    def test_or_combinator(self, store):
        samples = list(GraphDataset(store, label_filter="Document",
                                    property_filter={
                                        "$or": [
                                            {"score": {"$lte": 1.0}},
                                            {"score": {"$gte": 4.0}},
                                        ]
                                    }))
        assert len(samples) == 3  # 0.0, 1.0, 4.0
        assert all(s["score"] <= 1.0 or s["score"] >= 4.0 for s in samples)

    def test_not_combinator(self, store):
        samples = list(GraphDataset(store, label_filter="Document",
                                    property_filter={
                                        "$not": {"score": {"$gt": 2.0}}
                                    }))
        assert len(samples) == 3  # 0.0, 1.0, 2.0


# ---------------------------------------------------------------------------
# _worker_slice
# ---------------------------------------------------------------------------

class TestWorkerSlice:
    def _wi(self, num, id_):
        wi = MagicMock()
        wi.num_workers = num
        wi.id = id_
        return wi

    def test_covers_all(self):
        total, num_workers = 10, 3
        slices = [_worker_slice(total, self._wi(num_workers, i))
                  for i in range(num_workers)]
        covered = set()
        for start, end in slices:
            covered.update(range(start, end))
        assert covered == set(range(total))

    def test_no_overlap(self):
        slices = [_worker_slice(10, self._wi(3, i)) for i in range(3)]
        all_ids = list(itertools.chain.from_iterable(
            range(s, e) for s, e in slices))
        assert len(all_ids) == len(set(all_ids))


# ---------------------------------------------------------------------------
# graph_collate_fn
# ---------------------------------------------------------------------------

class TestCollate:
    def test_empty(self):
        assert graph_collate_fn([]) == {}

    def test_numerics_become_tensor(self):
        b = graph_collate_fn([{"x": 1.0}, {"x": 2.0}])
        assert isinstance(b["x"], torch.Tensor)
        assert b["x"].tolist() == [1.0, 2.0]

    def test_strings_stay_list(self):
        b = graph_collate_fn([{"l": "a"}, {"l": "b"}])
        assert b["l"] == ["a", "b"]

    def test_tensors_stacked(self):
        b = graph_collate_fn([
            {"f": torch.tensor([1.0, 2.0])},
            {"f": torch.tensor([3.0, 4.0])},
        ])
        assert b["f"].shape == (2, 2)


# ---------------------------------------------------------------------------
# GraphDataset — empty store
# ---------------------------------------------------------------------------

class TestEmpty:
    def test_yields_nothing(self, empty_store):
        assert list(GraphDataset(empty_store)) == []

    def test_label_filter_on_empty(self, empty_store):
        assert list(GraphDataset(empty_store, label_filter="X")) == []


# ---------------------------------------------------------------------------
# GraphDataset — populated store
# ---------------------------------------------------------------------------

class TestGraphDataset:
    def test_total_count(self, store):
        assert len(list(GraphDataset(store))) == 8

    def test_label_filter_str(self, store):
        samples = list(GraphDataset(store, label_filter="Document"))
        assert len(samples) == 5
        assert all(s["main_label"] == "Document" for s in samples)

    def test_label_filter_list(self, store):
        samples = list(GraphDataset(store, label_filter=["Document", "Page"]))
        assert {s["main_label"] for s in samples} == {"Document", "Page"}

    def test_property_filter(self, store):
        samples = list(GraphDataset(store, property_filter={"status": "ok"}))
        assert len(samples) == 5  # only Document nodes have status

    def test_combined_filters(self, store):
        samples = list(GraphDataset(store, label_filter="Document",
                                    property_filter={"score": 0.0}))
        assert len(samples) == 1

    def test_sample_keys(self, store):
        sample = next(iter(GraphDataset(store, label_filter="Document")))
        assert {"node_id", "main_label", "pk"}.issubset(sample.keys())

    def test_no_duplicate_node_ids(self, store):
        ids = [s["node_id"] for s in GraphDataset(store)]
        assert len(ids) == len(set(ids))

    def test_transform_applied(self, store):
        ds = GraphDataset(store, label_filter="Document",
                          transform=lambda s: {**s, "flag": True})
        assert all(s["flag"] for s in ds)


# ---------------------------------------------------------------------------
# Direct _node_attrs iteration (no list copy)
# ---------------------------------------------------------------------------

class TestZeroCopy:
    def test_iterates_dict_view_not_list(self, store):
        """_iter_networkx must not materialise get_node_ids() list."""
        import types
        ds = GraphDataset(store)
        # Patch get_node_ids to raise if called — zero-copy path must not use it
        store.get_node_ids = lambda: (_ for _ in ()).throw(
            AssertionError("get_node_ids should not be called"))
        # Should still work fine
        samples = list(ds)
        assert len(samples) == 8


# ---------------------------------------------------------------------------
# DataLoader integration
# ---------------------------------------------------------------------------

class TestDataLoader:
    def test_basic(self, store):
        loader = GraphDataLoader(
            GraphDataset(store, label_filter="Document"), batch_size=3)
        total = sum(len(b["node_id"]) for b in loader)
        assert total == 5

    def test_is_dataloader(self, store):
        assert isinstance(
            GraphDataLoader(GraphDataset(store), batch_size=4), DataLoader)

    def test_custom_collate(self, store):
        loader = GraphDataLoader(
            GraphDataset(store, label_filter="Document"),
            batch_size=3, collate_fn=list)
        for batch in loader:
            assert isinstance(batch, list)

    def test_batch_sizes(self, store):
        loader = GraphDataLoader(
            GraphDataset(store, label_filter="Document"), batch_size=2)
        sizes = [len(b["node_id"]) for b in loader]
        assert sum(sizes) == 5
        assert max(sizes) <= 2


# ---------------------------------------------------------------------------
# Full-graph heterogeneous edge_index_dict (e.g. for MetaPath2Vec)
# ---------------------------------------------------------------------------

@pytest.fixture()
def hetero_store(tmp_path):
    """2 Author, 3 Paper. AUTHORED: a0->p0,p1; a1->p2. CITES: p0->p1.

    ``p2`` has an AUTHORED edge but no CITES edge; ``p1`` is only ever a
    destination. Nothing is fully isolated on purpose — isolated nodes are
    out of scope for this loader (see docstring).
    """
    s = NetworkXGraph(str(tmp_path / "hetero.pkl"))
    authors = [Node(pk={"id": i}, main_label="Author", name=f"a{i}") for i in range(2)]
    papers = [Node(pk={"id": i}, main_label="Paper", title=f"p{i}") for i in range(3)]
    for a in authors:
        s.insertNode(a)
    for p in papers:
        s.insertNode(p)
    s.insertRelation(Relation(authors[0], papers[0], "AUTHORED"))
    s.insertRelation(Relation(authors[0], papers[1], "AUTHORED"))
    s.insertRelation(Relation(authors[1], papers[2], "AUTHORED"))
    s.insertRelation(Relation(papers[0], papers[1], "CITES"))
    return s


class TestHeteroEdgeIndex:
    def test_edge_types_present(self, hetero_store):
        edge_index_dict, num_nodes_dict, node_maps = to_hetero_edge_index_dict(hetero_store)
        assert set(edge_index_dict) == {
            ("Author", "AUTHORED", "Paper"),
            ("Paper", "CITES", "Paper"),
        }

    def test_edge_counts(self, hetero_store):
        edge_index_dict, _, _ = to_hetero_edge_index_dict(hetero_store)
        authored = edge_index_dict[("Author", "AUTHORED", "Paper")]
        cites = edge_index_dict[("Paper", "CITES", "Paper")]
        assert authored.shape == (2, 3)
        assert cites.shape == (2, 1)

    def test_num_nodes_by_type(self, hetero_store):
        _, num_nodes_dict, _ = to_hetero_edge_index_dict(hetero_store)
        assert num_nodes_dict == {"Author": 2, "Paper": 3}

    def test_node_maps_are_dense_local_indices(self, hetero_store):
        _, num_nodes_dict, node_maps = to_hetero_edge_index_dict(hetero_store)
        for ntype, n in num_nodes_dict.items():
            assert set(node_maps[ntype].values()) == set(range(n))

    def test_edge_index_uses_local_indices(self, hetero_store):
        edge_index_dict, _, node_maps = to_hetero_edge_index_dict(hetero_store)
        authored = edge_index_dict[("Author", "AUTHORED", "Paper")]
        max_src = int(authored[0].max())
        max_dst = int(authored[1].max())
        assert max_src < len(node_maps["Author"])
        assert max_dst < len(node_maps["Paper"])

    def test_no_edges_gives_empty_dict(self, empty_store):
        edge_index_dict, num_nodes_dict, node_maps = to_hetero_edge_index_dict(empty_store)
        assert edge_index_dict == {}
        assert num_nodes_dict == {}
        assert node_maps == {}


# ---------------------------------------------------------------------------
# edge_embeddings — combine node embeddings into an edge representation
# ---------------------------------------------------------------------------

class TestEdgeEmbeddings:
    @pytest.fixture()
    def node_embeddings(self):
        return torch.tensor([
            [1.0, 2.0],
            [3.0, 4.0],
            [5.0, 6.0],
        ])

    @pytest.fixture()
    def edge_index(self):
        return torch.tensor([[0, 1], [1, 2]], dtype=torch.long)

    def test_hadamard(self, node_embeddings, edge_index):
        out = edge_embeddings(node_embeddings, edge_index, op="hadamard")
        assert out.shape == (2, 2)
        assert out.tolist() == [[3.0, 8.0], [15.0, 24.0]]

    def test_concat(self, node_embeddings, edge_index):
        out = edge_embeddings(node_embeddings, edge_index, op="concat")
        assert out.shape == (2, 4)
        assert out.tolist() == [[1.0, 2.0, 3.0, 4.0], [3.0, 4.0, 5.0, 6.0]]

    def test_average(self, node_embeddings, edge_index):
        out = edge_embeddings(node_embeddings, edge_index, op="average")
        assert out.shape == (2, 2)
        assert out.tolist() == [[2.0, 3.0], [4.0, 5.0]]

    def test_l1(self, node_embeddings, edge_index):
        out = edge_embeddings(node_embeddings, edge_index, op="l1")
        assert out.tolist() == [[2.0, 2.0], [2.0, 2.0]]

    def test_l2(self, node_embeddings, edge_index):
        out = edge_embeddings(node_embeddings, edge_index, op="l2")
        assert out.tolist() == [[4.0, 4.0], [4.0, 4.0]]

    def test_dot(self, node_embeddings, edge_index):
        out = edge_embeddings(node_embeddings, edge_index, op="dot")
        assert out.shape == (2,)
        # (1*3 + 2*4)=11, (3*5 + 4*6)=39
        assert out.tolist() == [11.0, 39.0]

    def test_default_op_is_hadamard(self, node_embeddings, edge_index):
        out = edge_embeddings(node_embeddings, edge_index)
        assert out.tolist() == [[3.0, 8.0], [15.0, 24.0]]

    def test_unknown_op_raises(self, node_embeddings, edge_index):
        with pytest.raises(ValueError, match="hadamard"):
            edge_embeddings(node_embeddings, edge_index, op="bogus")

    def test_empty_edge_index(self, node_embeddings):
        empty = torch.zeros((2, 0), dtype=torch.long)
        out = edge_embeddings(node_embeddings, empty, op="hadamard")
        assert out.shape == (0, 2)
        out_dot = edge_embeddings(node_embeddings, empty, op="dot")
        assert out_dot.shape == (0,)


# ---------------------------------------------------------------------------
# split_edges_by_node_property — train/test edge split by a node attribute
# ---------------------------------------------------------------------------

class TestSplitEdgesByNodeProperty:
    def test_chain_split_by_threshold(self):
        # A(num=1) - B(num=2) - C(num=3), edges A->B, B->C
        edge_index = torch.tensor([[0, 1], [1, 2]], dtype=torch.long)
        node_values = torch.tensor([1.0, 2.0, 3.0])
        splits = split_edges_by_node_property(
            edge_index, node_values,
            train=lambda s, d: (s <= 2) & (d <= 2),
            test=lambda s, d: (s >= 2) & (d >= 2),
        )
        assert splits["train"].tolist() == [[0], [1]]
        assert splits["test"].tolist() == [[1], [2]]

    def test_three_way_train_val_test_split(self):
        # A chain of 4 nodes num=1..4, edges A-B, B-C, C-D.
        edge_index = torch.tensor([[0, 1, 2], [1, 2, 3]], dtype=torch.long)
        node_values = torch.tensor([1.0, 2.0, 3.0, 4.0])
        splits = split_edges_by_node_property(
            edge_index, node_values,
            train=lambda s, d: torch.maximum(s, d) <= 2,
            val=lambda s, d: (torch.minimum(s, d) >= 2) & (torch.maximum(s, d) <= 3),
            test=lambda s, d: torch.minimum(s, d) >= 3,
        )
        assert set(splits) == {"train", "val", "test"}
        assert splits["train"].tolist() == [[0], [1]]
        assert splits["val"].tolist() == [[1], [2]]
        assert splits["test"].tolist() == [[2], [3]]

    def test_edge_matching_no_condition_is_dropped(self):
        # A(num=1) -> C(num=3): fails both max<=2 (train) and min>=2 (test)
        edge_index = torch.tensor([[0], [1]], dtype=torch.long)
        node_values = torch.tensor([1.0, 3.0])
        splits = split_edges_by_node_property(
            edge_index, node_values,
            train=lambda s, d: (s <= 2) & (d <= 2),
            test=lambda s, d: (s >= 2) & (d >= 2),
        )
        assert splits["train"].shape == (2, 0)
        assert splits["test"].shape == (2, 0)

    def test_overlapping_conditions_raise(self):
        edge_index = torch.tensor([[0], [1]], dtype=torch.long)
        node_values = torch.tensor([1.0, 2.0])
        with pytest.raises(ValueError, match="overlap"):
            split_edges_by_node_property(
                edge_index, node_values,
                train=lambda s, d: (s <= 2) & (d <= 2),
                # Overlaps with train for this exact edge (1, 2).
                test=lambda s, d: (s <= 2) & (d <= 2),
            )

    def test_requires_at_least_two_splits(self):
        edge_index = torch.tensor([[0], [1]], dtype=torch.long)
        node_values = torch.tensor([1.0, 2.0])
        with pytest.raises(ValueError, match="at least two"):
            split_edges_by_node_property(
                edge_index, node_values,
                train=lambda s, d: (s <= 2) & (d <= 2),
            )

    def test_empty_edge_index(self):
        edge_index = torch.zeros((2, 0), dtype=torch.long)
        node_values = torch.tensor([1.0, 2.0, 3.0])
        splits = split_edges_by_node_property(
            edge_index, node_values,
            train=lambda s, d: (s <= 2) & (d <= 2),
            test=lambda s, d: (s >= 2) & (d >= 2),
        )
        assert splits["train"].shape == (2, 0)
        assert splits["test"].shape == (2, 0)


# ---------------------------------------------------------------------------
# Neo4j backend — node_id, property_filter fallback, stable SKIP/LIMIT order
# ---------------------------------------------------------------------------

def _neo4j_config():
    import os
    target = os.environ.get("NEO4J_TARGET", "DEV")

    def _env(key):
        return os.environ.get(f"NEO4J_{target.upper()}_{key}") or os.environ.get(f"NEO4J_{key}")

    url, user, password = _env("URL"), _env("USER"), _env("PASSWORD")
    if not url or not user or not password:
        return None
    return {"url": url, "user": user, "password": password, "database": _env("DATABASE") or "neo4j"}


@pytest.fixture()
def neo4j_store():
    config = _neo4j_config()
    if config is None:
        pytest.skip("NEO4J_DEV_* (or compatible NEO4J target/plain vars) not set")
    from cvcdocdb.neo4j_graph import Neo4jGraph

    graph = Neo4jGraph(url=config["url"], user=config["user"],
                        password=config["password"], database=config["database"])

    def _wipe():
        graph._tx = graph._session.begin_transaction()
        graph._tx.run("MATCH (n) DETACH DELETE n")
        graph._tx.commit()
        graph._tx = None

    _wipe()
    yield graph
    _wipe()
    graph.close()


@pytest.mark.slow
class TestGraphDatasetNeo4j:
    """Regression tests for the Neo4j path of GraphDataset._iter_neo4j."""

    def test_node_id_is_populated(self, neo4j_store):
        neo4j_store.insertNode(
            Node(pk={"id": 1}, main_label="Document", score=1.0), replace=True)
        samples = list(GraphDataset(neo4j_store, label_filter="Document"))
        assert len(samples) == 1
        assert samples[0]["node_id"] is not None
        assert isinstance(samples[0]["node_id"], int)

    def test_property_filter_or_combinator_applied(self, neo4j_store):
        for i, score in enumerate([0.5, 2.5, 4.5]):
            neo4j_store.insertNode(
                Node(pk={"id": i}, main_label="Document", score=score), replace=True)
        ds = GraphDataset(
            neo4j_store, label_filter="Document",
            property_filter={"$or": [{"score": {"$lte": 1}}, {"score": {"$gte": 4}}]},
        )
        assert sorted(s["score"] for s in ds) == [0.5, 4.5]

    def test_property_filter_regex_applied(self, neo4j_store):
        neo4j_store.insertNode(
            Node(pk={"id": 1}, main_label="Document", name="DocA"), replace=True)
        neo4j_store.insertNode(
            Node(pk={"id": 2}, main_label="Document", name="Other"), replace=True)
        ds = GraphDataset(
            neo4j_store, label_filter="Document",
            property_filter={"name": {"$regex": "^Doc"}},
        )
        assert sorted(s["name"] for s in ds) == ["DocA"]

    def test_no_duplicate_or_missing_nodes_across_chunks(self, neo4j_store):
        for i in range(25):
            neo4j_store.insertNode(Node(pk={"id": i}, main_label="Document"), replace=True)
        ds = GraphDataset(neo4j_store, label_filter="Document", chunk_size=7)
        node_ids = [s["node_id"] for s in ds]
        assert len(node_ids) == 25
        assert len(set(node_ids)) == 25
