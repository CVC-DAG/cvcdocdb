"""Tests for the graph viewer's interface language (cvcdocdb/viewer/static/i18n.js).

The page's texts come from one table per language (English, Catalan and
Spanish); ``language`` picks one in every entry point (``build_page``, the
HTTP server, ``--language`` and ``show()``). Data and the API's error
messages are not translated.
"""

import re
from importlib import resources

import pytest

from cvcdocdb.viewer import __main__ as cli
from cvcdocdb.viewer.page import LANGUAGES, build_page, with_options
from test.js_runner import needs_node, run_js

pytestmark = pytest.mark.unit


def _static(name: str) -> str:
    return (resources.files("cvcdocdb.viewer") / "static" / name).read_text("utf-8")


def _run(body: str):
    return run_js("i18n.js", "i18n", body)


def _keys(language: str):
    return set(_run(f"console.log(JSON.stringify(Object.keys(i18n.STRINGS[{language!r}])))"))


def test_languages():
    assert LANGUAGES == ("en", "ca", "es")


@needs_node
def test_every_language_has_the_same_keys():
    english = _keys("en")
    assert english
    for language in LANGUAGES:
        assert _keys(language) == english, language


@needs_node
def test_every_key_the_page_uses_exists():
    english = _keys("en")
    html = _static("index.html")
    used = set(re.findall(r'data-i18n(?:-[a-z-]+)?="([a-z_.]+)"', html))
    used |= set(re.findall(r'\bt\("([a-z_.]+)"', _static("viewer.js")))
    assert used
    assert used - english == set()


@needs_node
def test_parameters_are_filled_in():
    text = _run('console.log(JSON.stringify(i18n.translate("ca", "graph.count", {nodes: 3, relations: 2})))')
    assert text == "3 nodes · 2 relacions visibles"


@needs_node
def test_unknown_language_falls_back_to_english():
    assert _run('console.log(JSON.stringify(i18n.translate("xx", "toolbar.fit")))') == "Fit"
    assert _run('console.log(JSON.stringify(i18n.language("ca")))') == "ca"
    assert _run('console.log(JSON.stringify(i18n.language("xx")))') == "en"


@needs_node
def test_catalan_and_spanish_are_translated():
    assert _run('console.log(JSON.stringify(i18n.translate("ca", "search.go")))') == "Cerca"
    assert _run('console.log(JSON.stringify(i18n.translate("es", "search.go")))') == "Buscar"


def test_page_says_its_language():
    assert 'name="cvcdocdb-language" content="ca"' in build_page(language="ca")
    assert "cvcdocdb-language" not in build_page().split("</head>", 1)[0]
    page = with_options(_static("index.html"), language="es")
    assert 'name="cvcdocdb-language" content="es"' in page
    # The <html> tag is left alone: the notebook widget adds its attributes there.
    assert '<html lang="en">' in build_page(language="ca")


def test_i18n_script_is_inlined_before_the_viewer():
    page = build_page()
    assert page.index("CvcdocdbI18n") < page.index("window.CVCDOCDB_BRIDGE = true;")


@pytest.mark.parametrize("language", ["fr", "", "CA", "en-US", '"><script>'])
def test_unknown_language_is_rejected(language):
    with pytest.raises(ValueError):
        build_page(language=language)


def test_cli_takes_the_language(monkeypatch, tmp_path):
    from cvcdocdb import NetworkXGraph, Node

    path = tmp_path / "graph.pkl"
    graph = NetworkXGraph(persistence_path=str(path))
    graph.insertNode(Node(pk={"id": 1}, main_label="Person"))
    seen = {}
    monkeypatch.setattr(cli, "serve_viewer", lambda *args, **kwargs: seen.update(kwargs))
    cli.main([str(path), "--no-browser", "--language", "ca"])
    assert seen["language"] == "ca"


def test_server_page_uses_the_language(tmp_path):
    from cvcdocdb import NetworkXGraph
    from cvcdocdb.viewer.server import create_server

    server = create_server(NetworkXGraph(persistence_path=str(tmp_path / "g.pkl")), port=0, language="ca")
    try:
        assert b'name="cvcdocdb-language" content="ca"' in server.page
    finally:
        server.server_close()


def test_notebook_widget_uses_the_language(tmp_path):
    pytest.importorskip("anywidget")
    from cvcdocdb import NetworkXGraph
    from cvcdocdb.viewer import show

    widget = show(NetworkXGraph(persistence_path=str(tmp_path / "g.pkl")), language="es")
    assert 'name="cvcdocdb-language" content="es"' in widget._page
