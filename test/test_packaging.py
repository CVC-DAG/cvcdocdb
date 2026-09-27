"""The published wheel must declare the library's runtime dependencies.

Builds the package the same way it is published (``python -m build``:
sdist first, then the wheel *from the sdist*) and checks the wheel's
``Requires-Dist`` metadata. Regression test: ``setup.py`` used to read
``requirements.txt``, which isn't shipped in the sdist, so the published
wheel declared no dependencies at all.
"""

import email
import os
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
}
# Development-only tools listed in requirements.txt: never runtime deps.
DEV_ONLY = {"sphinx", "sphinx-rtd-theme", "nbsphinx", "notebook", "ipykernel", "ipywidgets", "matplotlib"}


def _name(requirement: str) -> str:
    """Normalised project name of a ``Requires-Dist`` value."""
    for sep in "<>=!~;[ ":
        requirement = requirement.split(sep)[0]
    return requirement.strip().lower().replace("_", "-")


@pytest.fixture(scope="module")
def wheel_metadata(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(out), ROOT],
        check=True,
        capture_output=True,
    )
    [wheel] = [p for p in out.iterdir() if p.suffix == ".whl"]
    with zipfile.ZipFile(wheel) as zf:
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
