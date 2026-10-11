"""Tests for the read-only data layer of the graph viewer (cvcdocdb.viewer.data).

The data layer only uses the public ``GraphStore`` API (dict ``query()`` and
parametrised read-only Cypher), so testing it on ``NetworkXGraph`` covers the
code path every backend runs.
"""

import pytest

from cvcdocdb.base import Node, Relation
from cvcdocdb.networkx_graph import NetworkXGraph
from cvcdocdb.viewer.data import (
    GraphViewerData,
    InvalidRequest,
    NodeNotFound,
)

pytestmark = pytest.mark.integration

ITEM_COUNT = 120


@pytest.fixture
def store(tmp_path):
    graph = NetworkXGraph(persistence_path=str(tmp_path / "viewer.pkl"))
    alice = Node(pk={"id": 1}, main_label="Person", alternative_labels=["Agent"],
                 name="Alice", age=30, bio="Likes graphs and Barcelona")
    bob = Node(pk={"id": 2}, main_label="Person", name="Bob", age=25)
    carol = Node(pk={"id": 3}, main_label="Person", name="Carol", age=41)
    bcn = Node(pk={"code": "BCN"}, main_label="City", name="Barcelona")
    gir = Node(pk={"code": "GRO"}, main_label="City", name="Girona")
    with graph.batch():
        for node in (alice, bob, carol, bcn, gir):
            graph.insertNode(node)
        graph.insertRelation(Relation(alice, bob, "KNOWS", since=2020))
        graph.insertRelation(Relation(carol, alice, "KNOWS"))
        graph.insertRelation(Relation(alice, bcn, "LIVES_IN"))
        graph.insertRelation(Relation(bob, gir, "LIVES_IN"))
        for i in range(ITEM_COUNT):
            graph.insertNode(Node(pk={"sku": i}, main_label="Item", title=f"item {i:03d}"))
    yield graph
    graph.close()


@pytest.fixture
def data(store):
    return GraphViewerData(store)


def _id_of(data, label, name):
    [item] = data.search(label=label, filters=[("name", "eq", name)])["items"]
    return item["id"]


# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------


def test_summary_counts_nodes_per_main_label(data):
    summary = data.summary()
    counts = {entry["label"]: entry["count"] for entry in summary["labels"]}
    assert counts == {"Person": 3, "City": 2, "Item": ITEM_COUNT}
    assert summary["node_count"] == 5 + ITEM_COUNT


def test_summary_counts_relation_types(data):
    summary = data.summary()
    counts = {entry["type"]: entry["count"] for entry in summary["relation_types"]}
    assert counts == {"KNOWS": 2, "LIVES_IN": 2}
    assert summary["relation_count"] == 4


def test_summary_reports_backend_and_is_sorted(data):
    summary = data.summary()
    assert summary["backend"] == "NetworkXGraph"
    labels = [entry["label"] for entry in summary["labels"]]
    assert labels == sorted(labels)


def test_summary_of_empty_store(tmp_path):
    graph = NetworkXGraph(persistence_path=str(tmp_path / "empty.pkl"))
    summary = GraphViewerData(graph).summary()
    assert summary["labels"] == [] and summary["relation_types"] == []
    assert summary["node_count"] == 0 and summary["relation_count"] == 0


# ----------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------


def test_search_by_label(data):
    result = data.search(label="City")
    assert result["total"] == 2
    assert result["total_is_exact"] is True
    assert sorted(item["caption"] for item in result["items"]) == ["Barcelona", "Girona"]


def test_search_items_are_node_summaries(data):
    [item] = data.search(label="Person", filters=[("name", "eq", "Alice")])["items"]
    assert item["main_label"] == "Person"
    assert item["labels"] == ["Person", "Agent"]
    assert item["pk"] == {"id": 1}
    assert item["properties"]["age"] == 30
    # Internal bookkeeping keys of the backend are not shown as properties.
    for internal in ("pk", "main_label", "labels"):
        assert internal not in item["properties"]


def test_search_filter_eq_coerces_numbers(data):
    # Values come from a URL as strings: "30" must match the integer 30.
    result = data.search(label="Person", filters=[("age", "eq", "30")])
    assert [item["caption"] for item in result["items"]] == ["Alice"]


def test_search_filter_contains_is_case_insensitive(data):
    result = data.search(filters=[("name", "contains", "ar")])
    assert sorted(item["caption"] for item in result["items"]) == ["Barcelona", "Carol"]


def test_search_filter_contains_treats_value_literally(data):
    # Regex metacharacters must not be interpreted.
    assert data.search(filters=[("name", "contains", ".*")])["total"] == 0


def test_search_filter_comparison_and_ne(data):
    older = data.search(label="Person", filters=[("age", "gte", "30")])
    assert sorted(item["caption"] for item in older["items"]) == ["Alice", "Carol"]
    not_bob = data.search(label="Person", filters=[("name", "ne", "Bob")])
    assert sorted(item["caption"] for item in not_bob["items"]) == ["Alice", "Carol"]


def test_search_filter_exists(data):
    result = data.search(filters=[("bio", "exists", "")])
    assert [item["caption"] for item in result["items"]] == ["Alice"]


def test_search_combines_filters(data):
    result = data.search(label="Person", filters=[("age", "lt", "40"), ("name", "contains", "o")])
    assert [item["caption"] for item in result["items"]] == ["Bob"]


def test_search_free_text_over_string_properties(data):
    result = data.search(text="barcelona")
    # City name and Alice's bio both mention it.
    assert sorted(item["caption"] for item in result["items"]) == ["Alice", "Barcelona"]


def test_search_free_text_with_label(data):
    result = data.search(label="City", text="BARCE")
    assert [item["caption"] for item in result["items"]] == ["Barcelona"]


def test_search_paging(data):
    first = data.search(label="Item", limit=50)
    second = data.search(label="Item", offset=50, limit=50)
    last = data.search(label="Item", offset=100, limit=50)
    assert first["total"] == ITEM_COUNT
    assert len(first["items"]) == 50 and len(second["items"]) == 50 and len(last["items"]) == 20
    ids = [i["id"] for page in (first, second, last) for i in page["items"]]
    assert len(set(ids)) == ITEM_COUNT
    assert first["offset"] == 0 and first["limit"] == 50


def test_search_limit_is_capped(store):
    data = GraphViewerData(store, max_page_size=10)
    result = data.search(label="Item", limit=10_000)
    assert len(result["items"]) == 10
    assert result["limit"] == 10


def test_search_scan_cap_reports_lower_bound(store):
    data = GraphViewerData(store, max_scan=30)
    result = data.search(label="Item", limit=10)
    assert result["total"] == 30
    assert result["total_is_exact"] is False


def test_search_offset_beyond_scan_cap_is_rejected(store):
    data = GraphViewerData(store, max_scan=30)
    with pytest.raises(InvalidRequest):
        data.search(label="Item", offset=30)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"label": "Person) MATCH (m"},
        {"label": ""},
        {"filters": [("na me", "eq", "x")]},
        {"filters": [("name", "regex", "x")]},
        {"filters": [("$or", "eq", "x")]},
        {"offset": -1},
        {"limit": 0},
        {"text": "x" * 1000},
        {"filters": [("name", "eq", "x")] * 50},
    ],
)
def test_search_rejects_invalid_input(data, kwargs):
    with pytest.raises(InvalidRequest):
        data.search(**kwargs)


def test_search_unknown_label_returns_nothing(data):
    result = data.search(label="Nope")
    assert result["items"] == [] and result["total"] == 0


# ----------------------------------------------------------------------
# Node detail
# ----------------------------------------------------------------------


def test_node_detail(data):
    node_id = _id_of(data, "Person", "Alice")
    node = data.node(node_id)
    assert node["id"] == node_id
    assert node["main_label"] == "Person"
    assert node["labels"] == ["Person", "Agent"]
    assert node["pk"] == {"id": 1}
    assert node["properties"]["bio"] == "Likes graphs and Barcelona"
    assert node["caption"] == "Alice"


def test_node_detail_unknown_id(data):
    with pytest.raises(NodeNotFound):
        data.node(987654)


@pytest.mark.parametrize("bad", ["1", None, 1.5, True])
def test_node_detail_rejects_non_integer_ids(data, bad):
    with pytest.raises(InvalidRequest):
        data.node(bad)


def test_detail_values_are_json_serialisable(tmp_path):
    import json

    graph = NetworkXGraph(persistence_path=str(tmp_path / "odd.pkl"))
    graph.insertNode(Node(pk={"id": 1}, main_label="Odd", blob=b"\x00\x01", when={1, 2}))
    data = GraphViewerData(graph)
    [item] = data.search(label="Odd")["items"]
    json.dumps(data.node(item["id"]))
    json.dumps(item)


# ----------------------------------------------------------------------
# Neighbours
# ----------------------------------------------------------------------


def test_neighbours_both_directions(data):
    alice = _id_of(data, "Person", "Alice")
    result = data.neighbours(alice)
    rels = {(r["type"], r["direction"], r["neighbour"]["caption"]) for r in result["relations"]}
    assert rels == {
        ("KNOWS", "out", "Bob"),
        ("KNOWS", "in", "Carol"),
        ("LIVES_IN", "out", "Barcelona"),
    }
    assert result["truncated"] is False
    assert result["node"]["id"] == alice


def test_neighbours_relation_has_endpoints_and_properties(data):
    alice = _id_of(data, "Person", "Alice")
    bob = _id_of(data, "Person", "Bob")
    [rel] = data.neighbours(alice, direction="out", relation_type="KNOWS")["relations"]
    assert rel["source"] == alice and rel["target"] == bob
    assert rel["properties"] == {"since": 2020}


def test_neighbours_direction_filter(data):
    alice = _id_of(data, "Person", "Alice")
    incoming = data.neighbours(alice, direction="in")["relations"]
    assert [r["neighbour"]["caption"] for r in incoming] == ["Carol"]


def test_neighbours_relation_type_filter(data):
    alice = _id_of(data, "Person", "Alice")
    rels = data.neighbours(alice, relation_type="LIVES_IN")["relations"]
    assert [r["neighbour"]["caption"] for r in rels] == ["Barcelona"]


def test_neighbours_limit_and_truncation(data):
    alice = _id_of(data, "Person", "Alice")
    result = data.neighbours(alice, limit=2)
    assert len(result["relations"]) == 2
    assert result["truncated"] is True


def test_neighbours_limit_is_capped(store):
    data = GraphViewerData(store, max_neighbours=1)
    alice = _id_of(data, "Person", "Alice")
    result = data.neighbours(alice, limit=500)
    assert len(result["relations"]) == 1 and result["truncated"] is True


def test_neighbours_unknown_node(data):
    with pytest.raises(NodeNotFound):
        data.neighbours(987654)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"direction": "sideways"},
        {"relation_type": "KNOWS]->(x) DETACH DELETE x //"},
        {"limit": 0},
    ],
)
def test_neighbours_reject_invalid_input(data, kwargs):
    alice = _id_of(data, "Person", "Alice")
    with pytest.raises(InvalidRequest):
        data.neighbours(alice, **kwargs)


def test_viewer_never_writes(store, tmp_path):
    """A full browse must leave the store unchanged."""
    before = (sorted(store.get_node_ids()), sorted(store.get_edges()))
    data = GraphViewerData(store)
    data.summary()
    for item in data.search(limit=200)["items"]:
        data.node(item["id"])
        data.neighbours(item["id"])
    assert (sorted(store.get_node_ids()), sorted(store.get_edges())) == before
