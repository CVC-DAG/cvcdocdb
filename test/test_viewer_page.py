"""Tests for the viewer page builder (cvcdocdb.viewer.page).

The page builder needs no optional dependency, so a host application (e.g. a
web app that embeds the viewer in a sandboxed iframe) can use it without
anywidget, and can pick the lighter layout and its own accent colour.
"""

import subprocess
import sys

import pytest

from cvcdocdb.viewer.page import LAYOUTS, build_page, page_attributes

pytestmark = pytest.mark.unit


def test_default_page_is_self_contained_bridge():
    page = build_page()
    assert 'src="static/' not in page and 'href="static/' not in page
    assert "window.CVCDOCDB_BRIDGE = true;" in page
    assert "connect-src 'none'" in page
    for script in page.split("<script>")[1:]:
        assert "</script" not in script.split("</script>", 1)[0]


def test_default_page_keeps_the_classic_layout():
    page = build_page()
    assert '<html lang="en">' in page
    assert "data-layout" not in page.split("<head>", 1)[0]


def test_light_layout_and_load_all_are_set_on_html():
    page = build_page(layout="light", load_all=True)
    html_tag = page.split("<head>", 1)[0]
    assert 'data-layout="light"' in html_tag and 'data-load-all="true"' in html_tag


def test_accent_colour_overrides_the_css_variable():
    page = build_page(accent="#00843D")
    assert "--accent: #00843D" in page


@pytest.mark.parametrize("accent", ["red", "#12", "#00843D; background: url(x)", "</style>"])
def test_invalid_accent_is_rejected(accent):
    with pytest.raises(ValueError):
        build_page(accent=accent)


def test_unknown_layout_is_rejected():
    with pytest.raises(ValueError):
        build_page(layout="fancy")
    assert LAYOUTS == ("classic", "light")


def test_page_attributes_for_the_http_server():
    assert page_attributes() == ""
    assert page_attributes(layout="light", load_all=True) == ' data-layout="light" data-load-all="true"'


def test_page_module_does_not_import_anywidget():
    code = "import sys, cvcdocdb.viewer.page as p; p.build_page(); print('anywidget' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_light_layout_has_its_styles_and_controls():
    page = build_page(layout="light")
    assert ':root[data-layout="light"]' in page
    assert 'id="toggle-table"' in page and 'id="detail-close"' in page


def test_notebook_build_page_is_the_same_page():
    anywidget = pytest.importorskip("anywidget")  # noqa: F841
    from cvcdocdb.viewer.notebook import build_page as notebook_build_page

    assert notebook_build_page() == build_page()


def test_extra_css_is_added_after_the_viewer_styles():
    page = build_page(extra_css="body { font-family: 'Open Sans', sans-serif; }")
    assert page.index("font-family: 'Open Sans'") > page.index(":root {")
    assert page.index("font-family: 'Open Sans'") < page.index("</head>")


@pytest.mark.parametrize("css", ["</style><script>alert(1)</script>", "a{}</STYLE >", "<!-- x"])
def test_extra_css_cannot_break_out_of_its_style_element(css):
    with pytest.raises(ValueError):
        build_page(extra_css=css)
