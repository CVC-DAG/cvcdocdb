"""Every tutorial notebook opens in Colab and Kaggle from its first cell.

The badges point at the notebook on GitHub (``main``, what PyPI users have).
In the Sphinx docs ``nbsphinx_prolog`` already adds the same badges to each
page, so the notebook's own badge cell is hidden there (``"nbsphinx": "hidden"``).
"""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
NOTEBOOKS = sorted((ROOT / "docs" / "tutorials" / "notebooks").rglob("*.ipynb"))
REPO = "CVC-DAG/cvcdocdb"


def test_there_are_tutorial_notebooks():
    assert NOTEBOOKS


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: str(p.relative_to(ROOT / "docs" / "tutorials" / "notebooks")))
def test_notebook_starts_with_colab_and_kaggle_badges(path):
    notebook = json.loads(path.read_text("utf-8"))
    cell = notebook["cells"][0]
    source = "".join(cell["source"])
    relative = path.relative_to(ROOT).as_posix()
    assert cell["cell_type"] == "markdown"
    assert cell["metadata"].get("nbsphinx") == "hidden"
    assert f"https://colab.research.google.com/github/{REPO}/blob/main/{relative}" in source
    assert f"https://www.kaggle.com/kernels/welcome?src=https://raw.githubusercontent.com/{REPO}/main/{relative}" in source
    assert "colab-badge.svg" in source and "open-in-kaggle.svg" in source
