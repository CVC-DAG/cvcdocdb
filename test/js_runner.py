"""Run the graph viewer's plain-JavaScript modules (cvcdocdb/viewer/static) with Node.js.

The modules have no DOM: they export their functions with ``module.exports``
under Node and as a ``window`` global in the page. Tests that use this are
skipped when ``node`` isn't installed.
"""

import json
import shutil
import subprocess
from importlib import resources

import pytest

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


def static_path(name: str) -> str:
    return str(resources.files("cvcdocdb.viewer") / "static" / name)


def run_js(module: str, variable: str, body: str):
    """Run *body* with *variable* bound to the static *module*; *body* must
    print one JSON value, which is returned."""
    script = f"const {variable} = require({json.dumps(static_path(module))});\n{body}"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(out.stdout)
