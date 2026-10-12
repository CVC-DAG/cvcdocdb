"""The published wheel must declare the library's runtime dependencies.

Builds the package the same way it is published (``python -m build``:
sdist first, then the wheel *from the sdist*) and checks the wheel's
``Requires-Dist`` metadata. Regression test: ``setup.py`` used to read
``requirements.txt``, which isn't shipped in the sdist, so the published
wheel declared no dependencies at all.
"""

import email
import os
import shutil
import subprocess
import sys
import zipfile

import pytest

pytest.importorskip("build")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RUNTIME = {"neo4j", "networkx", "numpy", "filelock", "tqdm"}
EXTRAS = {
    "rdf": {"rdflib", "pyyaml"},
    "schema": {"pyyaml"},
    "vector": {"hnswlib"},
    "torch": {"torch", "torch-geometric"},
    "graphrag": {"neo4j-graphrag"},
    "notebook": {"anywidget"},
}
# Files the graph viewer (cvcdocdb.viewer) serves.
VIEWER_STATIC_FILES = (
    "index.html",
    "viewer.js",
    "physics.js",
    "lod.js",
    "viewer.css",
    "widget.js",
    "vendor/cytoscape.min.js",
    "vendor/cytoscape.LICENSE.txt",
)
# Development-only tools listed in requirements.txt: never runtime deps.
DEV_ONLY = {"sphinx", "sphinx-rtd-theme", "nbsphinx", "notebook", "ipykernel", "ipywidgets", "matplotlib"}


def _name(requirement: str) -> str:
    """Normalised project name of a ``Requires-Dist`` value."""
    for sep in "<>=!~;[ ":
        requirement = requirement.split(sep)[0]
    return requirement.strip().lower().replace("_", "-")


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory):
    # Build from a copy: building in place rewrites the source tree's
    # *.egg-info, which an editable install reads its metadata from.
    src = tmp_path_factory.mktemp("src") / "cvcdocdb"
    shutil.copytree(
        ROOT, src,
        ignore=shutil.ignore_patterns(".git", ".venv", "dist", "build", "*.egg-info", "_build", "__pycache__"),
    )
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(out), str(src)],
        check=True,
        capture_output=True,
    )
    [wheel] = [p for p in out.iterdir() if p.suffix == ".whl"]
    return wheel


@pytest.fixture(scope="module")
def wheel_metadata(built_wheel):
    with zipfile.ZipFile(built_wheel) as zf:
        [meta] = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA")]
        return email.message_from_bytes(zf.read(meta))


@pytest.mark.unit
def test_wheel_declares_runtime_dependencies(wheel_metadata):
    unconditional = {
        _name(r) for r in wheel_metadata.get_all("Requires-Dist", []) if "extra ==" not in r
    }
    assert unconditional == RUNTIME


@pytest.mark.unit
def test_wheel_declares_optional_extras(wheel_metadata):
    assert set(wheel_metadata.get_all("Provides-Extra", [])) == set(EXTRAS)
    for extra, expected in EXTRAS.items():
        declared = {
            _name(r)
            for r in wheel_metadata.get_all("Requires-Dist", [])
            if f'extra == "{extra}"' in r
        }
        assert declared == expected, extra


@pytest.mark.unit
def test_wheel_has_no_development_only_dependencies(wheel_metadata):
    declared = {_name(r) for r in wheel_metadata.get_all("Requires-Dist", [])}
    assert not declared & DEV_ONLY


@pytest.mark.unit
def test_wheel_ships_graph_viewer_static_files(built_wheel):
    """The viewer's page, scripts and vendored cytoscape.js (with its license)
    must be inside the wheel, or ``python -m cvcdocdb.viewer`` serves 404s."""
    with zipfile.ZipFile(built_wheel) as zf:
        names = set(zf.namelist())
    for name in VIEWER_STATIC_FILES:
        assert f"cvcdocdb/viewer/static/{name}" in names, name


@pytest.mark.unit
def test_version_matches_installed_metadata():
    import importlib.metadata

    import cvcdocdb

    assert cvcdocdb.__version__ == importlib.metadata.version("cvcdocdb")

