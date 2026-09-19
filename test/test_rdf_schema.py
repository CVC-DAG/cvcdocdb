"""Tests for RDF ontology → DRM YAML schema conversion."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

try:
    import yaml
except ImportError:
    yaml = None

from cvcdocdb.rdf_schema import rdf_to_yaml, generate_classes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_ontology(path: str, ttl: str) -> None:
    """Write TTL content to path."""
    with open(path, "w") as f:
        f.write(ttl)


BASE_TTL = """\
@prefix ex: <http://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Document a owl:Class .

ex:Section a owl:Class ;
    rdfs:subClassOf ex:Document .

ex:Page a owl:Class ;
    rdfs:subClassOf ex:Section .

ex:Character a owl:Class .

ex:name a owl:DatatypeProperty ;
    rdfs:domain ex:Document, ex:Character .

ex:title a owl:DatatypeProperty ;
    rdfs:domain ex:Section .

ex:number a owl:DatatypeProperty ;
    rdfs:domain ex:Page .

ex:house a owl:DatatypeProperty ;
    rdfs:domain ex:Character .

ex:knows a owl:ObjectProperty ;
    rdfs:domain ex:Character ;
    rdfs:range ex:Character .
"""


# ---------------------------------------------------------------------------
# rdf_to_yaml
# ---------------------------------------------------------------------------


class RDFSchemaTest(unittest.TestCase):
    """Tests for converting RDF ontology to DRM YAML schema."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.ontology_path = os.path.join(self.tmpdir, "test.ttl")
        _write_ontology(self.ontology_path, BASE_TTL)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_yaml_string(self) -> None:
        """rdf_to_yaml returns a YAML string."""
        result = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        self.assertIsInstance(result, str)
        self.assertIn("labels:", result)
        self.assertIn("relationships:", result)

    def test_detects_hierarchy_section(self) -> None:
        """Section is subClassOf Document → WeakNode."""
        if yaml is None:
            self.skipTest("PyYAML not installed")
        result = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        data = yaml.safe_load(result)
        self.assertEqual(data["labels"]["Section"]["base_class"], "WeakNode")
        self.assertEqual(data["labels"]["Section"]["parent"], "Document")

    def test_detects_hierarchy_page(self) -> None:
        """Page is subClassOf Section → WeakNode."""
        if yaml is None:
            self.skipTest("PyYAML not installed")
        result = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        data = yaml.safe_load(result)
        self.assertEqual(data["labels"]["Page"]["base_class"], "WeakNode")
        self.assertEqual(data["labels"]["Page"]["parent"], "Section")

    def test_root_class_not_weaknode(self) -> None:
        """Document (no subClassOf) is a regular Node."""
        if yaml is None:
            self.skipTest("PyYAML not installed")
        result = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        data = yaml.safe_load(result)
        self.assertEqual(data["labels"]["Document"]["base_class"], "Node")
        self.assertNotIn("parent", data["labels"]["Document"])

    def test_character_not_weaknode(self) -> None:
        """Character (no subClassOf) is a regular Node."""
        if yaml is None:
            self.skipTest("PyYAML not installed")
        result = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        data = yaml.safe_load(result)
        self.assertEqual(data["labels"]["Character"]["base_class"], "Node")
        self.assertNotIn("parent", data["labels"]["Character"])

    def test_datatype_properties_mapped(self) -> None:
        """DatatypeProperties are mapped to label properties."""
        if yaml is None:
            self.skipTest("PyYAML not installed")
        result = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        data = yaml.safe_load(result)
        self.assertIn("name", data["labels"]["Document"]["properties"])
        self.assertIn("title", data["labels"]["Section"]["properties"])
        self.assertIn("number", data["labels"]["Page"]["properties"])
        self.assertIn("house", data["labels"]["Character"]["properties"])

    def test_object_properties_as_relations(self) -> None:
        """ObjectProperties are mapped to relationships."""
        if yaml is None:
            self.skipTest("PyYAML not installed")
        result = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        data = yaml.safe_load(result)
        self.assertIn("relationships", data)
        self.assertIn("knows", data["relationships"])
        self.assertEqual(data["relationships"]["knows"]["src"], "Character")
        self.assertEqual(data["relationships"]["knows"]["dst"], "Character")

    def test_owl_haskey_as_pk(self) -> None:
        """owl:hasKey properties are identified as primary keys."""
        if yaml is None:
            self.skipTest("PyYAML not installed")
        ttl = """\
@prefix ex: <http://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Document a owl:Class ;
    owl:hasKey ex:id .

ex:id a owl:DatatypeProperty .
"""
        path = os.path.join(self.tmpdir, "haskey.ttl")
        _write_ontology(path, ttl)
        result = rdf_to_yaml(path, "test", ontology_ns="http://example.org/")
        data = yaml.safe_load(result)
        self.assertIn("primary_key", data["labels"]["Document"])
        self.assertIn("id", data["labels"]["Document"]["primary_key"])

    def test_unique_identity_as_unique(self) -> None:
        """owl:uniqueIdentity marks properties as unique."""
        if yaml is None:
            self.skipTest("PyYAML not installed")
        ttl = """\
@prefix ex: <http://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Character a owl:Class ;
    owl:uniqueIdentity ex:uri .

ex:uri a owl:DatatypeProperty .
"""
        path = os.path.join(self.tmpdir, "unique.ttl")
        _write_ontology(path, ttl)
        result = rdf_to_yaml(path, "test", ontology_ns="http://example.org/")
        data = yaml.safe_load(result)
        self.assertIn("unique", data["labels"]["Character"])
        self.assertIn("uri", data["labels"]["Character"]["unique"])

    def test_deep_hierarchy(self) -> None:
        """Deep subClassOf chains are detected."""
        if yaml is None:
            self.skipTest("PyYAML not installed")
        ttl = """\
@prefix ex: <http://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Level1 a owl:Class .
ex:Level2 a owl:Class ; rdfs:subClassOf ex:Level1 .
ex:Level3 a owl:Class ; rdfs:subClassOf ex:Level2 .
ex:Level4 a owl:Class ; rdfs:subClassOf ex:Level3 .
"""
        path = os.path.join(self.tmpdir, "deep.ttl")
        _write_ontology(path, ttl)
        result = rdf_to_yaml(path, "test", ontology_ns="http://example.org/")
        data = yaml.safe_load(result)
        self.assertEqual(data["labels"]["Level2"]["parent"], "Level1")
        self.assertEqual(data["labels"]["Level3"]["parent"], "Level2")
        self.assertEqual(data["labels"]["Level4"]["parent"], "Level3")

    def test_object_property_with_range(self) -> None:
        """ObjectProperty with rdfs:range gives dst label."""
        if yaml is None:
            self.skipTest("PyYAML not installed")
        ttl = """\
@prefix ex: <http://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Person a owl:Class .
ex:Company a owl:Class .

ex:worksFor a owl:ObjectProperty ;
    rdfs:domain ex:Person ;
    rdfs:range ex:Company .
"""
        path = os.path.join(self.tmpdir, "range.ttl")
        _write_ontology(path, ttl)
        result = rdf_to_yaml(path, "test", ontology_ns="http://example.org/")
        data = yaml.safe_load(result)
        self.assertIn("worksFor", data["relationships"])
        self.assertEqual(data["relationships"]["worksFor"]["src"], "Person")
        self.assertEqual(data["relationships"]["worksFor"]["dst"], "Company")

    def test_property_type_inference(self) -> None:
        """DatatypeProperty type is inferred from range."""
        if yaml is None:
            self.skipTest("PyYAML not installed")
        result = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        data = yaml.safe_load(result)
        self.assertEqual(data["labels"]["Document"]["properties"]["name"], "string")
        self.assertEqual(data["labels"]["Section"]["properties"]["title"], "string")

    def test_empty_ontology(self) -> None:
        """Empty ontology generates valid YAML with empty sections."""
        ttl = "@prefix owl: <http://www.w3.org/2002/07/owl#> ."
        path = os.path.join(self.tmpdir, "empty.ttl")
        _write_ontology(path, ttl)
        result = rdf_to_yaml(path, "empty", ontology_ns="http://example.org/")
        self.assertIn("labels:", result)
        self.assertIn("relationships:", result)


# ---------------------------------------------------------------------------
# generate_classes
# ---------------------------------------------------------------------------


class GenerateClassesFromRDFTest(unittest.TestCase):
    """Tests for generate_classes() with RDF ontology YAML."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.ontology_path = os.path.join(self.tmpdir, "test.ttl")
        _write_ontology(self.ontology_path, BASE_TTL)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_generates_node_classes(self) -> None:
        """Node labels produce Node subclasses."""
        yaml_str = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        source = generate_classes(yaml_str)
        self.assertIn("class Character(Node):", source)
        self.assertIn("class Document(Node):", source)

    def test_generates_weaknode_classes(self) -> None:
        """WeakNode labels produce WeakNode subclasses."""
        yaml_str = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        source = generate_classes(yaml_str)
        self.assertIn("class Section(WeakNode):", source)
        self.assertIn("class Page(WeakNode):", source)

    def test_generates_relation_classes(self) -> None:
        """Regular relationships produce Relation subclasses."""
        yaml_str = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        source = generate_classes(yaml_str)
        self.assertIn("class Knows(Relation):", source)

    def test_generates_weakrelation_classes(self) -> None:
        """WeakRelations produce WeakRelation subclasses."""
        yaml_str = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        source = generate_classes(yaml_str)
        self.assertIn("class HasSection(WeakRelation):", source)
        self.assertIn("class HasPage(WeakRelation):", source)

    def test_imports_base_classes(self) -> None:
        """Generated source imports Node, WeakNode, Relation, WeakRelation."""
        yaml_str = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        source = generate_classes(yaml_str)
        self.assertIn("from cvcdocdb.base import Node, WeakNode, Relation, WeakRelation", source)

    def test_node_has_properties(self) -> None:
        """Node class has type-annotated property attributes (bare
        annotations, never assigned in __init__ — see schema_gen's
        module docstring for why)."""
        yaml_str = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        source = generate_classes(yaml_str)
        self.assertIn("house: Optional[str]", source)
        self.assertIn("name: Optional[str]", source)

    def test_weaknode_has_parent(self) -> None:
        """WeakNode class sets parent and parent_relation."""
        yaml_str = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        source = generate_classes(yaml_str)
        self.assertIn("parent=parent", source)
        self.assertIn("parent_relation='HAS_SECTION'", source)

    def test_generated_code_compiles(self) -> None:
        """Generated source is valid Python."""
        yaml_str = rdf_to_yaml(self.ontology_path, "test", ontology_ns="http://example.org/")
        source = generate_classes(yaml_str)
        compile(source, "<generated>", "exec")


if __name__ == "__main__":
    unittest.main()
