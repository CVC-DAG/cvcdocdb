"""Tests for the ``--changed`` test selection (test/changed_selection.py)."""

import pytest

from test.changed_selection import select_test_files

pytestmark = pytest.mark.unit

TESTS = [
    "test_viewer_cli.py",
    "test_viewer_data.py",
    "test_viewer_notebook.py",
    "test_viewer_server.py",
    "test_jena_graph.py",
    "test_jena_rdf.py",
    "test_schema_gen.py",
    "test_schema_gen_deterministic_parent.py",
    "test_graph_store_contract.py",
    "test_packaging.py",
    "test_node.py",
]


def test_package_change_selects_its_tests():
    assert select_test_files(["cvcdocdb/viewer/notebook.py"], TESTS) == {
        "test_viewer_cli.py", "test_viewer_data.py", "test_viewer_notebook.py", "test_viewer_server.py",
    }


def test_static_file_counts_for_its_package():
    assert "test_viewer_server.py" in select_test_files(["cvcdocdb/viewer/static/viewer.js"], TESTS)


def test_module_change_selects_tests_named_after_it():
    assert select_test_files(["cvcdocdb/schema_gen.py"], TESTS) == {
        "test_schema_gen.py", "test_schema_gen_deterministic_parent.py",
    }


def test_changed_test_file_selects_itself():
    assert select_test_files(["test/test_node.py"], TESTS) == {"test_node.py"}


def test_packaging_files_select_packaging_tests():
    for path in ("setup.py", "MANIFEST.in", "requirements.txt"):
        assert select_test_files([path], TESTS) == {"test_packaging.py"}, path


def test_documentation_selects_nothing():
    assert select_test_files(["README.md", "CHANGELOG.md", "docs/index.rst"], TESTS) == set()


def test_selections_add_up():
    assert select_test_files(["cvcdocdb/jena_graph.py", "test/test_node.py"], TESTS) == {
        "test_jena_graph.py", "test_node.py",
    }


@pytest.mark.parametrize(
    "path",
    [
        "cvcdocdb/base.py",            # core: every backend depends on it
        "cvcdocdb/graph_store.py",
        "cvcdocdb/__init__.py",
        "cvcdocdb/networkx_graph.py",  # no test is named after it
        "test/conftest.py",            # test infrastructure
        "test/propagation_scenarios.py",
    ],
)
def test_unmapped_or_core_change_runs_everything(path):
    assert select_test_files([path, "cvcdocdb/viewer/api.py"], TESTS) is None
