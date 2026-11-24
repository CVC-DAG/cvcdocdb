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


# ----------------------------------------------------------------------
# Relations among a set of nodes (one query for a whole canvas)
# ----------------------------------------------------------------------


def test_relations_among_returns_only_relations_between_the_given_nodes(data):
    alice, bob, carol = (_id_of(data, "Person", n) for n in ("Alice", "Bob", "Carol"))
    bcn = _id_of(data, "City", "Barcelona")
    result = data.relations_among([alice, bob, bcn])
    found = {(r["source"], r["type"], r["target"]) for r in result["relations"]}
    assert found == {(alice, "KNOWS", bob), (alice, "LIVES_IN", bcn)}
    assert result["truncated"] is False
    assert carol not in {r["source"] for r in result["relations"]}


def test_relations_among_keeps_relation_properties(data):
    alice, bob = _id_of(data, "Person", "Alice"), _id_of(data, "Person", "Bob")
    [rel] = data.relations_among([alice, bob])["relations"]
    assert rel["properties"] == {"since": 2020}


def test_relations_among_empty_and_unknown_ids(data):
    assert data.relations_among([])["relations"] == []
    assert data.relations_among([999999])["relations"] == []


def test_relations_among_caps_relations(store):
    data = GraphViewerData(store, max_relations=1)
    ids = [_id_of(data, "Person", n) for n in ("Alice", "Bob", "Carol")]
    result = data.relations_among(ids)
    assert len(result["relations"]) == 1 and result["truncated"] is True


@pytest.mark.parametrize("ids", [["x"], [True], list(range(10_000))])
def test_relations_among_rejects_bad_ids(data, ids):
    with pytest.raises(InvalidRequest):
        data.relations_among(ids)


def test_max_relations_must_be_positive(store):
    with pytest.raises(ValueError):
        GraphViewerData(store, max_relations=0)


# ----------------------------------------------------------------------
# Several labels at once (the page filters by one or more node types)
# ----------------------------------------------------------------------


def test_search_by_several_labels(data):
    result = data.search(labels=["Person", "City"])
    assert result["total"] == 5
    assert {item["main_label"] for item in result["items"]} == {"Person", "City"}


def test_search_label_and_labels_are_combined(data):
    result = data.search(label="City", labels=["Person"])
    assert {item["main_label"] for item in result["items"]} == {"Person", "City"}


def test_search_several_labels_with_filters_and_text(data):
    result = data.search(labels=["Person", "City"], text="barcelona")
    names = {item["properties"]["name"] for item in result["items"]}
    assert names == {"Alice", "Barcelona"}  # Alice's bio mentions Barcelona
    result = data.search(labels=["Person", "City"], filters=[("name", "eq", "Girona")])
    assert [item["properties"]["name"] for item in result["items"]] == ["Girona"]


def test_search_several_labels_pages_over_all_of_them(data):
    first = data.search(labels=["Person", "City"], limit=3)
    second = data.search(labels=["Person", "City"], offset=3, limit=3)
    ids = [item["id"] for item in first["items"] + second["items"]]
    assert len(ids) == 5 and len(set(ids)) == 5


def test_search_several_labels_repeated_is_harmless(data):
    assert data.search(labels=["Person", "Person"])["total"] == 3


@pytest.mark.parametrize("labels", [["Person", "bad label"], ["X"] * 100])
def test_search_rejects_bad_or_too_many_labels(data, labels):
    with pytest.raises(InvalidRequest):
        data.search(labels=labels)


# ----------------------------------------------------------------------
# Cypher search (read-only)
# ----------------------------------------------------------------------


def test_cypher_returns_columns_and_scalar_rows(data):
    result = data.cypher("MATCH (p:Person) RETURN p.name AS name, p.age AS age ORDER BY p.age")
    assert result["columns"] == ["name", "age"]
    assert result["rows"] == [["Bob", 25], ["Alice", 30], ["Carol", 41]]
    assert result["truncated"] is False
    assert result["nodes"] == []


def test_cypher_node_cells_are_resolved_to_viewer_ids(data):
    alice = _id_of(data, "Person", "Alice")
    result = data.cypher("MATCH (p:Person) WHERE p.name = 'Alice' RETURN p")
    [[cell]] = result["rows"]
    assert cell["kind"] == "node"
    assert cell["id"] == alice
    assert cell["labels"][0] == "Person"
    assert cell["caption"] == "Alice"
    assert [node["id"] for node in result["nodes"]] == [alice]
    assert result["nodes"][0]["properties"]["name"] == "Alice"


def test_cypher_relation_cells_are_described(data):
    result = data.cypher("MATCH (:Person)-[r:KNOWS]->(:Person) RETURN r.since AS since, type(r) AS t ORDER BY since")
    assert result["rows"] == [[None, "KNOWS"], [2020, "KNOWS"]] or result["rows"] == [[2020, "KNOWS"], [None, "KNOWS"]]
    cell = data.cypher("MATCH (:Person)-[r:KNOWS]->(:Person) WHERE r.since = 2020 RETURN r")["rows"][0][0]
    assert cell["kind"] == "relation"
    assert cell["relation_type"] == "KNOWS"
    assert cell["properties"] == {"since": 2020}


def test_cypher_rows_are_capped_and_flagged(store):
    data = GraphViewerData(store, max_cypher_rows=10)
    result = data.cypher("MATCH (i:Item) RETURN i.title AS title")
    assert len(result["rows"]) == 10
    assert result["truncated"] is True


def test_cypher_limit_argument_is_capped_by_the_maximum(store):
    data = GraphViewerData(store, max_cypher_rows=10)
    assert len(data.cypher("MATCH (i:Item) RETURN i.title AS t", limit=500)["rows"]) == 10
    assert len(data.cypher("MATCH (i:Item) RETURN i.title AS t", limit=3)["rows"]) == 3


def test_cypher_nodes_on_the_canvas_are_capped(store):
    data = GraphViewerData(store, max_cypher_nodes=5)
    result = data.cypher("MATCH (i:Item) RETURN i")
    assert len(result["nodes"]) == 5
    assert result["unresolved"] >= 1


@pytest.mark.parametrize("query", [
    "CREATE (n:Person {name: 'Mallory'})",
    "MATCH (n) DETACH DELETE n",
    "MATCH (n:Person) SET n.name = 'x' RETURN n",
    "MATCH (n) REMOVE n.name",
    "MERGE (n:City {name: 'x'})",
    "CALL db.labels()",
    "LOAD CSV FROM 'http://x' AS row RETURN row",
    "MATCH (n) FOREACH (x IN [1] | SET n.a = x)",
    "match (n) delete n",
])
def test_cypher_refuses_anything_that_is_not_read_only(data, store, query):
    before = data.summary()["node_count"]
    with pytest.raises(InvalidRequest, match="read-only"):
        data.cypher(query)
    assert data.summary()["node_count"] == before


@pytest.mark.parametrize("query", ["", "   ", None, 42])
def test_cypher_refuses_empty_or_non_text(data, query):
    with pytest.raises(InvalidRequest):
        data.cypher(query)


def test_cypher_refuses_overlong_queries(data):
    with pytest.raises(InvalidRequest, match="limited"):
        data.cypher("MATCH (n) RETURN n " + " " * 5000)


def test_cypher_syntax_errors_are_a_bad_request(data):
    with pytest.raises(InvalidRequest, match="Cypher"):
        data.cypher("MATCH (n RETURN")


def test_cypher_forbidden_words_inside_strings_are_fine(data):
    result = data.cypher("MATCH (p:Person) WHERE p.name = 'CREATE' RETURN p.name AS name")
    assert result["rows"] == []


def test_cypher_values_are_json_safe_and_size_limited(store):
    store.insertNode(Node(pk={"id": 500}, main_label="Doc", text="x" * 5000))
    data = GraphViewerData(store)
    [[cell]] = data.cypher("MATCH (d:Doc) RETURN d.text AS text")["rows"]
    assert len(cell) < 5000
