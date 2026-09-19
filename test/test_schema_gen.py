"""Tests for the YAML schema-to-Python-class generator."""

from __future__ import annotations

import os
import shutil
import tempfile
import textwrap
import unittest

try:
    import yaml
except ImportError:
    yaml = None

from cvcdocdb.base import Node, Relation, WeakNode, WeakRelation
from cvcdocdb.networkx_graph import NetworkXGraph
from cvcdocdb.schema_gen import generate_classes, generate_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hierarchy_graph() -> NetworkXGraph:
    """Create a NetworkXGraph with WeakNode hierarchies."""
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "hierarchy.pkl")
    g = NetworkXGraph(persistence_path=path)
    g._temp_path = tmp

    doc = Node(pk={"id": 1}, main_label="Document", name="Informe")
    g.insertNode(doc, replace=True)

    sec = WeakNode(
        pk={"title": "Introducció"}, main_label="Section",
        parent=doc, parent_relation="HAS_SECTION",
    )
    g.insertNode(sec, replace=True)

    page = WeakNode(
        pk={"number": 1}, main_label="Page",
        parent=sec, parent_relation="HAS_PAGE",
    )
    g.insertNode(page, replace=True)

    g.insertNode(
        Node(pk={"name": "Jon Snow"}, main_label="Character", house="Stark"),
        replace=True,
    )
    g.insertNode(
        Node(pk={"name": "Arya"}, main_label="Character", house="Stark"),
        replace=True,
    )
    g.insertRelation(
        Relation(
            Node(pk={"name": "Jon Snow"}, main_label="Character"),
            Node(pk={"name": "Arya"}, main_label="Character"),
            "KNOWS",
            strength="strong",
        ),
        update=True,
    )

    return g


# ---------------------------------------------------------------------------
# generate_classes
# ---------------------------------------------------------------------------


class GenerateClassesTest(unittest.TestCase):
    """Tests for generate_classes() — YAML string → Python source."""

    def setUp(self) -> None:
        self.graph = _make_hierarchy_graph()

    def tearDown(self) -> None:
        path = getattr(self.graph, "_temp_path", None)
        try:
            self.graph.close()
        except Exception:
            pass
        if path and os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)

    def _yaml(self) -> str:
        return self.graph.schema_yaml("test")

    def test_generates_node_classes(self) -> None:
        """Node labels produce Node subclasses."""
        source = generate_classes(self._yaml())
        self.assertIn("class Character(Node):", source)
        self.assertIn("class Document(Node):", source)

    def test_generates_weaknode_classes(self) -> None:
        """WeakNode labels produce WeakNode subclasses."""
        source = generate_classes(self._yaml())
        self.assertIn("class Section(WeakNode):", source)
        self.assertIn("class Page(WeakNode):", source)

    def test_generates_relation_classes(self) -> None:
        """Regular relationships produce Relation subclasses."""
        source = generate_classes(self._yaml())
        self.assertIn("class Knows(Relation):", source)

    def test_generates_weakrelation_classes(self) -> None:
        """WeakRelations produce WeakRelation subclasses."""
        source = generate_classes(self._yaml())
        self.assertIn("class HasSection(WeakRelation):", source)
        self.assertIn("class HasPage(WeakRelation):", source)

    def test_imports_base_classes(self) -> None:
        """Generated source imports Node, WeakNode, Relation, WeakRelation."""
        source = generate_classes(self._yaml())
        self.assertIn("from cvcdocdb.base import Node, WeakNode, Relation, WeakRelation", source)

    def test_node_has_pk_and_properties(self) -> None:
        """Node class has pk parameter and type-annotated properties."""
        source = generate_classes(self._yaml())
        # Character has house and name properties, declared as bare
        # annotations (never assigned in __init__ — see module docstring).
        self.assertIn("house: Optional[str]", source)
        self.assertIn("name: Optional[str]", source)
        self.assertIn("pk=pk", source)

    def test_properties_are_not_assigned_in_init(self) -> None:
        """Property annotations must never become an assignment in
        __init__ — that's exactly the pattern that corrupted partial
        merges (see test_generated_class_partial_update_preserves_fields)."""
        source = generate_classes(self._yaml())
        self.assertNotIn("self.house = ", source)
        self.assertNotIn("self.name = ", source)
        self.assertNotIn("kwargs.get(", source)

    def test_weaknode_has_parent(self) -> None:
        """WeakNode class sets parent and parent_relation."""
        source = generate_classes(self._yaml())
        # Section is WeakNode of Document
        self.assertIn("parent=parent", source)
        self.assertIn("parent_relation='HAS_SECTION'", source)

    def test_empty_schema(self) -> None:
        """Empty schema generates minimal valid Python."""
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "empty.pkl")
            g = NetworkXGraph(persistence_path=path)
            src = generate_classes(g.schema_yaml("empty"))
            g.close()
            # Should be valid Python
            compile(src, "<string>", "exec")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_generated_code_compiles(self) -> None:
        """Generated source is valid Python."""
        source = generate_classes(self._yaml())
        compile(source, "<generated>", "exec")

    def test_generated_code_imports(self) -> None:
        """Generated source can be imported (no syntax errors)."""
        source = generate_classes(self._yaml())
        # At minimum, check it has the expected structure
        self.assertIn("from cvcdocdb.base import", source)
        self.assertIn("class", source)


# ---------------------------------------------------------------------------
# generate_file
# ---------------------------------------------------------------------------


class GeneratedClassPartialUpdateTest(unittest.TestCase):
    """Regression test: a generated entity class must never corrupt an
    existing node's untouched fields on a partial `insertNode(update=True)`
    merge (bug found integrating this generator into a downstream app —
    every unspecified field, including PK fields, was defaulting to the
    literal type-name string, e.g. `email="string"`, wiping real data)."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "users.pkl")
        self.graph = NetworkXGraph(persistence_path=path)
        self.graph.insertNode(Node(
            pk={"email": "a@example.org"}, main_label="User",
            nom="Real Name", password="hash123", role="admin", is_owner=True,
        ))

    def tearDown(self) -> None:
        self.graph.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _generated_user_class(self):
        source = generate_classes(self.graph.schema_yaml("users_db"))
        namespace: dict = {}
        exec(compile(source, "<generated>", "exec"), namespace)
        return namespace["User"]

    def test_generated_class_partial_update_preserves_fields(self) -> None:
        User = self._generated_user_class()

        # Only touch is_owner — nom/password/role/email must survive untouched.
        self.graph.insertNode(
            User(pk={"email": "a@example.org"}, is_owner=False), update=True,
        )

        props = self.graph.query({"main_label": "User"})[0]["properties"]
        self.assertEqual(props["email"], "a@example.org")
        self.assertEqual(props["nom"], "Real Name")
        self.assertEqual(props["password"], "hash123")
        self.assertEqual(props["role"], "admin")
        self.assertEqual(props["is_owner"], False)


class GenerateFileTest(unittest.TestCase):
    """Tests for generate_file() — writes a .py file."""

    def setUp(self) -> None:
        self.graph = _make_hierarchy_graph()

    def tearDown(self) -> None:
        path = getattr(self.graph, "_temp_path", None)
        try:
            self.graph.close()
        except Exception:
            pass
        if path and os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)

    def test_writes_file(self) -> None:
        """generate_file writes a .py file to disk."""
        output_dir = tempfile.mkdtemp()
        try:
            out_path = generate_file(self.graph, "test", output_dir)
            self.assertTrue(os.path.exists(out_path))
            self.assertTrue(out_path.endswith(".py"))
            with open(out_path) as f:
                content = f.read()
            self.assertIn("class Character(Node):", content)
            self.assertIn("class Section(WeakNode):", content)
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_file_compiles(self) -> None:
        """Generated file is valid Python."""
        output_dir = tempfile.mkdtemp()
        try:
            out_path = generate_file(self.graph, "test", output_dir)
            with open(out_path) as f:
                compile(f.read(), out_path, "exec")
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Security: schema-derived values must not be able to inject code
# ---------------------------------------------------------------------------


class SchemaGenSecurityTest(unittest.TestCase):
    """A malicious/untrusted YAML schema (e.g. from an RDF ontology's
    rdfs:comment) must never be able to inject executable Python into the
    generated source, and schema-derived identifiers must be validated.
    """

    def test_malicious_doc_cannot_break_out_of_docstring(self) -> None:
        """A doc value containing an embedded triple-quote must not let
        attacker-chosen statements execute when the generated file runs."""
        malicious_doc = 'A"""\n    globals()["PWNED"] = True\n    x = "'
        data = {
            "labels": {
                "Evil": {
                    "class_name": "Evil",
                    "doc": malicious_doc,
                    "properties": {},
                    "primary_key": [],
                }
            }
        }
        yaml_source = yaml.safe_dump(data)
        source = generate_classes(yaml_source)

        namespace: dict = {}
        exec(compile(source, "<generated>", "exec"), namespace)
        self.assertNotIn("PWNED", namespace)

    def test_invalid_class_name_is_rejected(self) -> None:
        """A class_name that isn't a valid Python identifier must be rejected,
        not spliced into `class ...(Node):` verbatim."""
        data = {
            "labels": {
                "Bad": {
                    "class_name": "Evil):\n    pass\nimport os\nclass Evil2(Node",
                    "properties": {},
                    "primary_key": [],
                }
            }
        }
        yaml_source = yaml.safe_dump(data)
        with self.assertRaises(ValueError):
            generate_classes(yaml_source)

    def test_invalid_property_name_is_rejected(self) -> None:
        """A property name that isn't a valid Python identifier must be
        rejected, not spliced into `self.<name> = ...` verbatim."""
        data = {
            "labels": {
                "Good": {
                    "class_name": "Good",
                    "properties": {"x = 1\nimport os\nos.system('id')\nself.y": "v"},
                    "primary_key": [],
                }
            }
        }
        yaml_source = yaml.safe_dump(data)
        with self.assertRaises(ValueError):
            generate_classes(yaml_source)


if __name__ == "__main__":
    unittest.main()
