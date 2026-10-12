"""Tests for the graph viewer's command line (python -m cvcdocdb.viewer) and
for the static files being reachable through the installed package."""

from importlib import resources

import pytest

from cvcdocdb.base import Node
from cvcdocdb.networkx_graph import NetworkXGraph
from cvcdocdb.viewer import __main__ as cli
from cvcdocdb.viewer.data import GraphViewerData

pytestmark = pytest.mark.integration

STATIC_FILES = (
    "index.html",
    "viewer.js",
    "physics.js",
    "lod.js",
    "viewer.css",
    "vendor/cytoscape.min.js",
    "vendor/cytoscape.LICENSE.txt",
)


@pytest.mark.parametrize("name", STATIC_FILES)
def test_static_files_ship_with_the_package(name):
    static = resources.files("cvcdocdb.viewer") / "static"
    target = static.joinpath(*name.split("/"))
    assert target.is_file(), name
    assert target.read_bytes()


def test_vendored_cytoscape_keeps_its_license_notice():
    static = resources.files("cvcdocdb.viewer") / "static" / "vendor"
    assert "The Cytoscape Consortium" in (static / "cytoscape.LICENSE.txt").read_text("utf-8")
    assert "Permission is hereby granted" in (static / "cytoscape.min.js").read_text("utf-8")[:2000]


def test_front_end_makes_no_external_requests():
    static = resources.files("cvcdocdb.viewer") / "static"
    for name in ("index.html", "viewer.js", "physics.js", "lod.js", "viewer.css"):
        text = (static / name).read_text("utf-8")
        assert "http://" not in text and "https://" not in text, name


def test_cli_opens_a_networkx_pickle(tmp_path, monkeypatch):
    path = tmp_path / "graph.pkl"
    graph = NetworkXGraph(persistence_path=str(path))
    graph.insertNode(Node(pk={"id": 1}, main_label="Person", name="Alice"))

    calls = {}

    def fake_serve(store, host, port, token, open_browser, **options):
        calls.update(store=store, host=host, port=port, token=token, open_browser=open_browser, options=options)
        calls["labels"] = [e["label"] for e in GraphViewerData(store).summary()["labels"]]

    monkeypatch.setattr(cli, "serve_viewer", fake_serve)
    assert cli.main([str(path), "--port", "0", "--no-browser"]) == 0
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 0
    assert calls["token"] is None
    assert calls["open_browser"] is False
    assert calls["labels"] == ["Person"]
    assert calls["options"] == {"layout": "classic", "load_all": False, "accent": None, "extra_css": None}


def test_cli_missing_pickle_is_an_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([str(tmp_path / "missing.pkl")])
    assert exc.value.code != 0
    assert not (tmp_path / "missing.pkl").exists()


def test_cli_token_from_environment(tmp_path, monkeypatch):
    path = tmp_path / "graph.pkl"
    NetworkXGraph(persistence_path=str(path)).insertNode(Node(pk={"id": 1}, main_label="X"))
    monkeypatch.setenv("CVCDOCDB_VIEWER_TOKEN", "from-env")
    seen = {}
    monkeypatch.setattr(cli, "serve_viewer", lambda store, **kw: seen.update(kw))
    cli.main([str(path), "--no-browser"])
    assert seen["token"] == "from-env"


def test_cli_requires_a_source(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code != 0


def test_cli_remote_backend_needs_url(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--backend", "neo4j"])
    assert exc.value.code != 0


def test_cli_builds_remote_backend_with_password_from_env(monkeypatch):
    created = {}

    class FakeNeo4j:
        def __init__(self, url, user, password, database=None):
            created.update(url=url, user=user, password=password, database=database)

        def close(self):
            created["closed"] = True

    monkeypatch.setattr(cli, "_backend_class", lambda name: FakeNeo4j)
    monkeypatch.setenv("CVCDOCDB_PASSWORD", "pw")
    monkeypatch.setattr(cli, "serve_viewer", lambda store, **kw: None)
    cli.main(["--backend", "neo4j", "--url", "bolt://db:7687", "--user", "neo4j",
              "--database", "abc", "--no-browser"])
    assert created == {"url": "bolt://db:7687", "user": "neo4j", "password": "pw",
                       "database": "abc", "closed": True}


def test_cli_page_options(tmp_path, monkeypatch):
    path = tmp_path / "graph.pkl"
    NetworkXGraph(persistence_path=str(path)).insertNode(Node(pk={"id": 1}, main_label="Person"))
    css = tmp_path / "host.css"
    css.write_text("body { font-family: serif; }", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(cli, "serve_viewer", lambda store, **kw: seen.update(kw))
    argv = [str(path), "--no-browser", "--layout", "light", "--load-all", "--accent", "#00843D", "--css", str(css)]
    assert cli.main(argv) == 0
    assert (seen["layout"], seen["load_all"], seen["accent"]) == ("light", True, "#00843D")
    assert seen["extra_css"] == "body { font-family: serif; }"


def test_cli_rejects_unknown_layout(tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.main([str(tmp_path / "x.pkl"), "--layout", "fancy"])
