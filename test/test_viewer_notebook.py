"""Tests for the graph viewer's notebook widget (cvcdocdb.viewer.notebook)
and the request routing it shares with the HTTP server (cvcdocdb.viewer.api)."""

import json
import subprocess
import sys

import pytest

from cvcdocdb.base import Node, Relation
from cvcdocdb.networkx_graph import NetworkXGraph
from cvcdocdb.viewer import GraphViewerData, InvalidRequest, NodeNotFound
from cvcdocdb.viewer.api import UnknownEndpoint, handle_api

pytestmark = pytest.mark.integration


@pytest.fixture
def store(tmp_path):
    graph = NetworkXGraph(persistence_path=str(tmp_path / "notebook.pkl"))
    alice = Node(pk={"id": 1}, main_label="Person", name="Alice", age=30)
    bob = Node(pk={"id": 2}, main_label="Person", name="Bob", age=25)
    bcn = Node(pk={"code": "BCN"}, main_label="City", name="Barcelona")
    with graph.batch():
        for node in (alice, bob, bcn):
            graph.insertNode(node)
        graph.insertRelation(Relation(alice, bob, "KNOWS"))
        graph.insertRelation(Relation(alice, bcn, "LIVES_IN"))
    yield graph
    graph.close()


@pytest.fixture
def data(store):
    return GraphViewerData(store)


def _alice_id(data):
    [alice] = data.search(text="Alice")["items"]
    return alice["id"]


# ---------------------------------------------------------------------------
# handle_api: the routing shared by the server and the widget
# ---------------------------------------------------------------------------


def test_handle_api_summary(data):
    summary = handle_api(data, "/api/summary", {})
    assert summary["node_count"] == 3 and summary["relation_count"] == 2


def test_handle_api_search_with_label_and_filters(data):
    result = handle_api(data, "/api/nodes", {"label": ["Person"], "f": ["age:gt:26"]})
    assert [item["caption"] for item in result["items"]] == ["Alice"]


def test_handle_api_node_and_neighbours(data):
    alice = _alice_id(data)
    node = handle_api(data, f"/api/nodes/{alice}", {})
    assert node["properties"]["name"] == "Alice"
    neighbours = handle_api(data, f"/api/nodes/{alice}/neighbours", {"type": ["KNOWS"]})
    assert [r["neighbour"]["caption"] for r in neighbours["relations"]] == ["Bob"]


@pytest.mark.parametrize(
    "path, query",
    [
        ("/api/nodes", {"limit": ["many"]}),
        # Several labels are allowed (node-type filter); a bad one still is not.
        ("/api/nodes", {"label": ["Person", "Pers on!"]}),
        ("/api/nodes", {"f": ["no-operator"]}),
        ("/api/nodes/abc", {}),
    ],
)
def test_handle_api_rejects_bad_parameters(data, path, query):
    with pytest.raises(InvalidRequest):
        handle_api(data, path, query)


def test_handle_api_relations(data):
    alice = _alice_id(data)
    [bob] = data.search(text="Bob")["items"]
    result = handle_api(data, "/api/relations", {"ids": [str(alice), str(bob["id"])]})
    assert [(r["source"], r["type"], r["target"]) for r in result["relations"]] == [(alice, "KNOWS", bob["id"])]


def test_handle_api_relations_rejects_bad_ids(data):
    with pytest.raises(InvalidRequest):
        handle_api(data, "/api/relations", {"ids": ["abc"]})


def test_handle_api_unknown_node(data):
    with pytest.raises(NodeNotFound):
        handle_api(data, "/api/nodes/999999", {})


@pytest.mark.parametrize("path", ["/api/nope", "/api", "/static/viewer.js", "/api/nodes/1/2/3"])
def test_handle_api_unknown_endpoint(data, path):
    with pytest.raises(UnknownEndpoint):
        handle_api(data, path, {})


# ---------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------

anywidget = pytest.importorskip("anywidget")

from cvcdocdb.viewer.notebook import GraphViewerWidget, build_page  # noqa: E402


@pytest.fixture
def widget(store):
    w = GraphViewerWidget(store)
    sent = []
    w.send = lambda content, buffers=None: sent.append(content)
    w.sent = sent
    yield w
    w.close()


def _ask(widget, path, params=None, request_id=1):
    widget._handle_message(widget, {"type": "cvcdocdb-api", "id": request_id, "path": path,
                                    "params": params or []}, [])
    [reply] = widget.sent
    widget.sent.clear()
    assert reply["type"] == "cvcdocdb-api" and reply["id"] == request_id
    return reply


def test_widget_is_an_anywidget(widget):
    assert isinstance(widget, anywidget.AnyWidget)
    assert widget.height == 650


def test_widget_answers_summary(widget):
    reply = _ask(widget, "api/summary", request_id=7)
    assert reply["ok"] is True and reply["status"] == 200
    assert reply["body"]["node_count"] == 3


def test_widget_passes_repeated_params_through(widget):
    reply = _ask(widget, "api/nodes", [["label", "Person"], ["f", "age:gte:25"], ["f", "name:contains:Bo"]])
    assert [item["caption"] for item in reply["body"]["items"]] == ["Bob"]


def test_widget_neighbours(widget):
    alice = _alice_id(widget.data)
    reply = _ask(widget, f"api/nodes/{alice}/neighbours", [["direction", "out"]])
    assert sorted(r["neighbour"]["caption"] for r in reply["body"]["relations"]) == ["Barcelona", "Bob"]


@pytest.mark.parametrize(
    "path, params, status",
    [
        ("api/nodes", [["limit", "x"]], 400),
        ("api/nodes/999999", [], 404),
        ("api/nope", [], 404),
        ("../server.py", [], 404),
    ],
)
def test_widget_reports_errors(widget, path, params, status):
    reply = _ask(widget, path, params)
    assert reply["ok"] is False and reply["status"] == status
    assert isinstance(reply["body"]["error"], str)


def test_widget_hides_internal_errors(widget, monkeypatch):
    def boom():
        raise RuntimeError("secret internals")

    monkeypatch.setattr(widget.data, "summary", boom)
    reply = _ask(widget, "api/summary")
    assert reply["ok"] is False and reply["status"] == 500
    assert "secret" not in reply["body"]["error"]


def test_widget_ignores_foreign_and_malformed_messages(widget):
    widget._handle_message(widget, {"type": "something-else"}, [])
    widget._handle_message(widget, "not a dict", [])
    widget._handle_message(widget, {"type": "cvcdocdb-api", "id": 1, "path": 42, "params": []}, [])
    assert [r["status"] for r in widget.sent] == [400]


def test_widget_replies_are_json(widget):
    reply = _ask(widget, "api/summary")
    assert json.loads(json.dumps(reply)) == reply


def test_widget_accepts_configured_data(data):
    w = GraphViewerWidget(data, height=400)
    assert w.data is data and w.height == 400
    w.close()


def test_page_is_self_contained():
    page = build_page()
    # Everything inlined: no request for the server's static files.
    assert 'src="static/' not in page and 'href="static/' not in page
    assert "cytoscape" in page and "postMessage" in page
    # The inlined scripts can't close their own <script> element early.
    for script in page.split("<script>")[1:]:
        assert "</script" not in script.split("</script>", 1)[0]
    # No network access from inside the frame.
    assert "connect-src 'none'" in page


def test_show_returns_widget(store):
    from cvcdocdb.viewer import show

    w = show(store, height=500)
    assert isinstance(w, GraphViewerWidget) and w.height == 500
    w.close()


def test_viewer_package_does_not_import_anywidget():
    code = "import sys, cvcdocdb.viewer; print('anywidget' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_page_can_follow_the_notebook_theme():
    page = build_page()
    # The widget sets <html data-theme> and sends "cvcdocdb-theme" messages.
    assert '<html lang="en">' in page
    assert ':root[data-theme="dark"]' in page and ':root:not([data-theme="light"])' in page
    assert "cvcdocdb-theme" in page


def test_refresh_asks_the_page_to_reload(widget):
    widget.refresh()
    assert widget.sent == [{"type": "cvcdocdb-refresh"}]


def test_load_all_is_off_by_default_and_synced(store):
    w = GraphViewerWidget(store)
    assert w.load_all is False and "load_all" in w.keys
    w.close()
    w = GraphViewerWidget(store, load_all=True)
    assert w.load_all is True
    w.close()


def test_show_passes_load_all(store):
    from cvcdocdb.viewer import show

    w = show(store, load_all=True)
    assert w.load_all is True
    w.close()


def test_page_has_a_refresh_button():
    page = build_page()
    assert 'id="graph-refresh"' in page and "cvcdocdb-refresh" in page and "data-load-all" in page
