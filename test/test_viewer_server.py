"""Tests for the graph viewer's HTTP server (cvcdocdb.viewer.server)."""

import http.client
import json
import threading
from urllib.parse import quote

import pytest

from cvcdocdb.base import Node, Relation
from cvcdocdb.networkx_graph import NetworkXGraph
from cvcdocdb.viewer import create_server, serve_viewer  # noqa: F401  (public API)

pytestmark = pytest.mark.integration

TOKEN = "s3cret-token"


@pytest.fixture
def store(tmp_path):
    graph = NetworkXGraph(persistence_path=str(tmp_path / "server.pkl"))
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


def _start(store, **kwargs):
    server = create_server(store, host="127.0.0.1", port=0, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture
def server(store):
    server, thread = _start(store)
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def token_server(store):
    server, thread = _start(store, token=TOKEN)
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _request(server, path, method="GET", headers=None, body=None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        payload = response.read()
        return response.status, dict(response.getheaders()), payload
    finally:
        conn.close()


def _json(server, path, **kwargs):
    status, headers, payload = _request(server, path, **kwargs)
    return status, json.loads(payload.decode("utf-8")) if payload else None


def _alice_id(server):
    status, body = _json(server, "/api/nodes?label=Person&f=" + quote("name:eq:Alice"))
    assert status == 200
    return body["items"][0]["id"]


# ----------------------------------------------------------------------
# Server basics
# ----------------------------------------------------------------------


def test_binds_to_loopback_by_default(store):
    server = create_server(store, port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
        assert server.url.startswith("http://127.0.0.1:")
    finally:
        server.server_close()


def test_index_page_is_served(server):
    status, headers, payload = _request(server, "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    html = payload.decode("utf-8")
    assert "cytoscape.min.js" in html and "viewer.js" in html


@pytest.mark.parametrize(
    "path, content_type",
    [
        ("/static/viewer.js", "text/javascript"),
        ("/static/viewer.css", "text/css"),
        ("/static/vendor/cytoscape.min.js", "text/javascript"),
    ],
)
def test_static_assets_are_served(server, path, content_type):
    status, headers, payload = _request(server, path)
    assert status == 200
    assert headers["Content-Type"].startswith(content_type)
    assert payload


@pytest.mark.parametrize(
    "path",
    ["/static/../server.py", "/static/%2e%2e/server.py", "/static/missing.js", "/static/", "/nope"],
)
def test_unknown_or_traversal_paths_are_404(server, path):
    status, _, _ = _request(server, path)
    assert status == 404


def test_security_headers(server):
    _, headers, _ = _request(server, "/")
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    _, api_headers, _ = _request(server, "/api/summary")
    assert api_headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_non_get_methods_are_rejected(server, method):
    status, headers, _ = _request(server, "/api/summary", method=method, body=b"{}")
    assert status == 405
    assert headers["Allow"] == "GET"


def test_foreign_host_header_is_rejected(server):
    # DNS-rebinding protection while bound to loopback.
    status, _, _ = _request(server, "/api/summary", headers={"Host": "evil.example:80"})
    assert status == 403


# ----------------------------------------------------------------------
# JSON endpoints
# ----------------------------------------------------------------------


def test_summary_endpoint(server):
    status, body = _json(server, "/api/summary")
    assert status == 200
    assert {e["label"]: e["count"] for e in body["labels"]} == {"Person": 2, "City": 1}
    assert {e["type"] for e in body["relation_types"]} == {"KNOWS", "LIVES_IN"}


def test_nodes_endpoint_with_filters_and_paging(server):
    status, body = _json(server, "/api/nodes?label=Person&limit=1&offset=1")
    assert status == 200
    assert body["total"] == 2 and len(body["items"]) == 1 and body["offset"] == 1


def test_nodes_endpoint_filter_value_may_contain_colons(server):
    status, body = _json(server, "/api/nodes?f=" + quote("name:contains:a:b"))
    assert status == 200 and body["total"] == 0


def test_nodes_endpoint_free_text(server):
    status, body = _json(server, "/api/nodes?q=barc")
    assert status == 200
    assert [i["caption"] for i in body["items"]] == ["Barcelona"]


def test_node_detail_endpoint(server):
    alice = _alice_id(server)
    status, body = _json(server, f"/api/nodes/{alice}")
    assert status == 200
    assert body["properties"]["age"] == 30


def test_neighbours_endpoint(server):
    alice = _alice_id(server)
    status, body = _json(server, f"/api/nodes/{alice}/neighbours?direction=out&type=KNOWS")
    assert status == 200
    assert [r["neighbour"]["caption"] for r in body["relations"]] == ["Bob"]


def test_unknown_node_is_404(server):
    status, body = _json(server, "/api/nodes/987654")
    assert status == 404 and "error" in body
    status, _ = _json(server, "/api/nodes/987654/neighbours")
    assert status == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/nodes/abc",
        "/api/nodes?limit=abc",
        "/api/nodes?offset=-3",
        "/api/nodes?f=nocolon",
        "/api/nodes?f=" + quote("name:regex:.*"),
        "/api/nodes?label=" + quote("A) DETACH DELETE (n"),
        "/api/nodes/1/neighbours?direction=up",
        "/api/nodes/1/neighbours?limit=x",
    ],
)
def test_invalid_input_is_400(server, path):
    status, body = _json(server, path)
    assert status == 400
    assert "error" in body


def test_unknown_api_path_is_404(server):
    status, body = _json(server, "/api/everything")
    assert status == 404


# ----------------------------------------------------------------------
# Token
# ----------------------------------------------------------------------


def test_token_is_required_on_api(token_server):
    status, body = _json(token_server, "/api/summary")
    assert status == 401
    status, _ = _json(token_server, "/api/summary?token=wrong")
    assert status == 401


def test_token_in_query_string(token_server):
    status, _ = _json(token_server, f"/api/summary?token={TOKEN}")
    assert status == 200


def test_token_in_bearer_header(token_server):
    status, _ = _json(token_server, "/api/summary", headers={"Authorization": f"Bearer {TOKEN}"})
    assert status == 200
    status, _ = _json(token_server, "/api/summary", headers={"Authorization": "Bearer nope"})
    assert status == 401


def test_static_page_needs_no_token(token_server):
    # The page holds no data; it forwards the token from its own URL to /api.
    status, _, _ = _request(token_server, "/")
    assert status == 200


def test_serve_viewer_runs_until_shutdown(store, monkeypatch):
    """serve_viewer() builds the server and blocks in serve_forever()."""
    import cvcdocdb.viewer.server as server_module

    seen = {}

    def fake_serve_forever(self, poll_interval=0.5):
        seen["url"] = self.url

    monkeypatch.setattr(server_module.ViewerServer, "serve_forever", fake_serve_forever)
    serve_viewer(store, port=0, open_browser=False)
    assert seen["url"].startswith("http://127.0.0.1:")
