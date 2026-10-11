"""Which test files to run for a set of changed files (``pytest --changed``).

The rules are deliberately conservative — when in doubt, run everything:

* ``test/test_*.py`` — that file.
* ``cvcdocdb/<name>.py`` or anything under ``cvcdocdb/<name>/`` — the test
  files whose name starts with ``test_<name>`` (``viewer`` →
  ``test_viewer_*.py``, ``jena_graph`` → ``test_jena_graph.py``).
* Packaging files (``setup.py``, ``MANIFEST.in``, ``requirements*.txt``,
  ``pyproject.toml``) — ``test_packaging.py``.
* Documentation (``*.md``, ``*.rst``, ``docs/``, notebooks) — nothing.
* The core modules everything depends on, a source file no test is named
  after, test infrastructure (``conftest.py``, helpers) or any other file —
  the whole suite (``None``).
"""

from __future__ import annotations

import posixpath
import subprocess
from typing import Iterable, List, Optional, Set

# Every backend and test builds on these: a change runs the whole suite.
CORE_MODULES = {"__init__", "base", "graph_store"}
PACKAGING_FILES = {"setup.py", "MANIFEST.in", "pyproject.toml", "setup.cfg"}
PACKAGING_TESTS = {"test_packaging.py"}
DOC_SUFFIXES = (".md", ".rst", ".txt", ".ipynb", ".png", ".svg")


def _tests_for(path: str, test_files: List[str]) -> Optional[Set[str]]:
    """Test files for one changed *path*; ``None`` means "run everything"."""
    name = posixpath.basename(path)
    if path.startswith("test/"):
        if name.startswith("test_") and name.endswith(".py"):
            return {name} & set(test_files)
        return None
    if name in PACKAGING_FILES or (name.startswith("requirements") and name.endswith(".txt")):
        return set(PACKAGING_TESTS) & set(test_files)
    if path.startswith("cvcdocdb/"):
        module = path[len("cvcdocdb/"):].split("/", 1)[0]
        module = module[:-3] if module.endswith(".py") else module
        if module in CORE_MODULES:
            return None
        selected = {t for t in test_files if t.startswith(f"test_{module}")}
        return selected or None
    if path.startswith("docs/") or path.endswith(DOC_SUFFIXES):
        return set()
    return None


def select_test_files(changed: Iterable[str], test_files: Iterable[str]) -> Optional[Set[str]]:
    """Test files (basenames) to run for the *changed* paths (relative to the
    repository root), or ``None`` to run the whole suite."""
    test_files = list(test_files)
    selected: Set[str] = set()
    for path in changed:
        tests = _tests_for(path, test_files)
        if tests is None:
            return None
        selected |= tests
    return selected


def changed_files(root: str, base: str) -> List[str]:
    """Files changed against *base* (committed or not) plus untracked ones."""
    def git(*args: str) -> List[str]:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
        return [line for line in out.stdout.splitlines() if line]

    return sorted(set(git("diff", "--name-only", base)) | set(git("ls-files", "--others", "--exclude-standard")))
