"""Tests for the graph viewer's node repulsion (cvcdocdb/viewer/static/physics.js).

The force simulation is plain JavaScript with no DOM, so these tests run it
with Node.js (skipped when ``node`` isn't installed). The page uses it while
a node is dragged: the other nodes keep a distance from each other and
make way for the dragged one, and its neighbours follow it.
"""

from importlib import resources

import pytest

from cvcdocdb.viewer.page import build_page
from test.js_runner import needs_node, run_js

pytestmark = pytest.mark.unit


def _run(body: str):
    """Run *body* with ``physics`` = the module; it must print one JSON value."""
    return run_js("physics.js", "physics", body)


def _settle(nodes: str, edges: str = "[]", steps: int = 400, options: str = "{}", rest: float = 0.01) -> str:
    return f"""
const nodes = {nodes};
const edges = {edges};
let moved = Infinity, n = 0;
for (; n < {steps} && moved > {rest}; n++) moved = physics.step(nodes, edges, {options});
console.log(JSON.stringify({{nodes: nodes, steps: n, moved: moved}}));
"""


def _dist(a, b) -> float:
    return ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5


@needs_node
def test_overlapping_nodes_are_pushed_apart():
    result = _run(_settle("[{x: 0, y: 0}, {x: 1, y: 0}, {x: 0, y: 1}]"))
    a, b, c = result["nodes"]
    gap = _run("console.log(JSON.stringify(physics.DEFAULTS.minDistance))")
    for p, q in ((a, b), (a, c), (b, c)):
        assert _dist(p, q) >= gap


@needs_node
def test_nodes_on_the_same_spot_still_separate():
    result = _run(_settle("[{x: 5, y: 5}, {x: 5, y: 5}]"))
    a, b = result["nodes"]
    assert _dist(a, b) > 1


@needs_node
def test_fixed_node_does_not_move_and_pushes_the_others():
    result = _run(_settle("[{x: 0, y: 0, fixed: true}, {x: 2, y: 0}]"))
    fixed, other = result["nodes"]
    assert (fixed["x"], fixed["y"]) == (0, 0)
    assert _dist(fixed, other) >= _run("console.log(JSON.stringify(physics.DEFAULTS.minDistance))")


@needs_node
def test_related_nodes_are_pulled_towards_the_spring_length():
    result = _run(_settle("[{x: 0, y: 0, fixed: true}, {x: 1000, y: 0}]", "[[0, 1]]", steps=2000))
    a, b = result["nodes"]
    length = _run("console.log(JSON.stringify(physics.DEFAULTS.springLength))")
    assert _dist(a, b) < 2 * length


@needs_node
def test_moving_a_node_drags_its_neighbour_and_shoves_the_rest():
    # Settle a chain a - b and a lone node c, then drag a (fixed) onto c.
    result = _run("""
const nodes = [{x: 0, y: 0}, {x: 80, y: 0}, {x: 400, y: 0}];
const edges = [[0, 1]];
for (let i = 0; i < 300; i++) physics.step(nodes, edges);
const before = nodes.map(n => ({x: n.x, y: n.y}));
nodes[0].fixed = true; nodes[0].x = nodes[2].x; nodes[0].y = nodes[2].y + 1;
for (let i = 0; i < 600; i++) physics.step(nodes, edges);
console.log(JSON.stringify({before: before, after: nodes}));
""")
    before, after = result["before"], result["after"]
    gap = _run("console.log(JSON.stringify(physics.DEFAULTS.minDistance))")
    # The lone node made way for the dragged one...
    assert _dist(after[0], after[2]) >= gap
    assert _dist(after[2], before[2]) > 1
    # ...and the neighbour followed it.
    assert _dist(after[1], after[0]) < _dist(before[1], after[0])


@needs_node
def test_simulation_comes_to_rest():
    # Even from a tangle (25 nodes of a chain bunched together); "at rest" is
    # what the page uses to stop the animation (PHYSICS_REST in viewer.js).
    nodes = "[" + ",".join(f"{{x: {i % 5}, y: {i // 5}}}" for i in range(25)) + "]"
    edges = "[" + ",".join(f"[{i}, {i + 1}]" for i in range(24)) + "]"
    result = _run(_settle(nodes, edges, steps=3000, rest=0.05))
    assert result["moved"] <= 0.05
    assert result["steps"] < 3000


@needs_node
def test_after_a_drag_the_graph_comes_to_rest_quickly():
    result = _run("""
const N = 60;
const nodes = Array.from({length: N}, (_, i) => ({x: Math.cos(i) * i * 8, y: Math.sin(i) * i * 8}));
const edges = [];
for (let i = 1; i < N; i++) edges.push([Math.floor((i - 1) / 3), i]);
for (let n = 0; n < 20000 && physics.step(nodes, edges) >= 0.05; n++);
const held = nodes[5];
held.fixed = true;
for (let k = 0; k < 30; k++) { held.x += 200 / 30; physics.step(nodes, edges); }
held.fixed = false;
let n = 0;
for (; n < 2000 && physics.step(nodes, edges) >= 0.05; n++);
console.log(JSON.stringify(n));
""")
    assert result < 300  # 4 steps per frame in the page: about a second


@needs_node
def test_options_override_the_defaults():
    result = _run(_settle("[{x: 0, y: 0, fixed: true}, {x: 1, y: 0}]", options="{minDistance: 200}"))
    a, b = result["nodes"]
    assert _dist(a, b) >= 200


@needs_node
def test_hidden_nodes_are_ignored():
    result = _run(_settle("[{x: 0, y: 0}, {x: 1, y: 0, ignore: true}]"))
    a, b = result["nodes"]
    assert (b["x"], b["y"]) == (1, 0)
    assert (a["x"], a["y"]) == (0, 0)


def test_physics_script_ships_and_is_inlined_in_the_page():
    source = (resources.files("cvcdocdb.viewer") / "static" / "physics.js").read_text("utf-8")
    page = build_page()
    assert source.strip().splitlines()[0] in page
    assert page.index("CvcdocdbPhysics") < page.index("window.CVCDOCDB_BRIDGE = true;")


def test_page_has_the_physics_toggle():
    page = build_page()
    assert 'id="physics-toggle"' in page and "startPhysics" in page
