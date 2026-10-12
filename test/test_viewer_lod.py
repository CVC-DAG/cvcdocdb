"""Tests for the graph viewer's level of detail (cvcdocdb/viewer/static/lod.js).

With many nodes, the canvas zoomed out shows only the most connected ones
(drawn bigger) and zooming in reveals the rest; the selected node, its
neighbours and the expanded nodes are always shown. The rules are plain
JavaScript, run here with Node.js (skipped when ``node`` isn't installed).
"""

import pytest

from cvcdocdb.viewer.page import build_page
from test.js_runner import needs_node, run_js

pytestmark = pytest.mark.unit


def _run(body: str):
    return run_js("lod.js", "lod", body)


def _keep(total: int, ratio: float) -> int:
    return _run(f"console.log(JSON.stringify(lod.keepCount({total}, {ratio})))")


@needs_node
def test_small_graphs_always_show_every_node():
    minimum = _run("console.log(JSON.stringify(lod.MIN_NODES))")
    for total in (0, 1, minimum):
        for ratio in (0.01, 1, 10):
            assert _keep(total, ratio) == total


@needs_node
def test_at_the_layout_zoom_a_quarter_is_shown_but_never_fewer_than_the_minimum():
    minimum, fraction = _run("console.log(JSON.stringify([lod.MIN_NODES, lod.BASE_FRACTION]))")
    assert _keep(400, 1) == round(400 * fraction)
    assert _keep(minimum + 10, 1) == minimum
    assert _keep(1000, 0.001) == minimum


@needs_node
def test_zooming_in_reveals_more_until_every_node_is_shown():
    counts = _run("console.log(JSON.stringify([0.5, 1, 1.5, 2, 3, 4, 8].map(r => lod.keepCount(200, r))))")
    assert counts == sorted(counts)
    assert counts[0] < counts[-1] == 200
    assert _keep(200, 1 / _run("console.log(JSON.stringify(lod.BASE_FRACTION))")) == 200


@needs_node
def test_nodes_are_ranked_by_degree_most_connected_first():
    ranked = _run("""
const nodes = [{id: "a", degree: 1}, {id: "hub", degree: 9}, {id: "b", degree: 4}, {id: "c", degree: 4}];
console.log(JSON.stringify(lod.rank(nodes).map(n => n.id)));
""")
    assert ranked[0] == "hub" and ranked[-1] == "a"
    assert ranked[1:3] == ["b", "c"]  # ties keep their order


@needs_node
def test_node_size_grows_with_the_square_root_of_the_degree():
    small, large = _run("console.log(JSON.stringify([lod.MIN_SIZE, lod.MAX_SIZE]))")
    sizes = _run("console.log(JSON.stringify([0, 1, 4, 9, 16].map(d => lod.nodeSize(d, 16))))")
    assert sizes[0] == small and sizes[-1] == large
    assert sizes == sorted(sizes)
    # Square root: a quarter of the top degree gets half the extra size.
    assert sizes[2] == small + round((large - small) / 2)
    assert _run("console.log(JSON.stringify(lod.nodeSize(0, 0)))") == small


@needs_node
def test_hidden_nodes_are_the_least_connected_except_the_pinned_ones():
    hidden = _run("""
const ranked = ["n0", "n1", "n2", "n3", "n4", "n5"];
console.log(JSON.stringify(lod.hiddenIds(ranked, 2, new Set(["n5"]))));
""")
    assert hidden == ["n2", "n3", "n4"]


@needs_node
def test_nothing_is_hidden_when_every_node_is_kept():
    assert _run('console.log(JSON.stringify(lod.hiddenIds(["a", "b"], 5, new Set())))') == []


def test_lod_script_is_inlined_in_the_page_before_the_viewer():
    page = build_page()
    assert page.index("CvcdocdbLod") < page.index("window.CVCDOCDB_BRIDGE = true;")
    assert "CvcdocdbLod.keepCount" in page
