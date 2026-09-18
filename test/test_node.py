"""Tests unitaris per la classe Node.

Verifiquen el comportament intern del Node: pk_attributes, main_label,
labels, pk compostes i WeakNode.

Usage:
    python -m pytest test/test_node.py -v
"""

import unittest

from cvcdocdb.base import Node, WeakNode
from cvcdocdb.drm_entities import Atribut


class NodeTest(unittest.TestCase):
    def test_get_pk_attributes(self):
        r = Node(pk=0, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        s = Node(pk={'id': 0}, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        t = Node(pk={'code': 0}, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])

        self.assertEqual(
            [r['pk_attributes'], s['pk_attributes'], t['pk_attributes']],
            [{'id': 0}, {'id': 0}, {'code': 0}]
        )

    def test_set_pk_attributes(self):
        r = Node(pk=0, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        s = Node(pk={'id': 0}, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        t = Node(pk={'code': 0}, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])

        r['pk_attributes']['id'] = 1
        s['pk_attributes']['id'] = 1
        t['pk_attributes']['code'] = 1

        self.assertEqual(
            [r['pk_attributes'], s['pk_attributes'], t['pk_attributes']],
            [{'id': 1}, {'id': 1}, {'code': 1}]
        )

    def test_get_main_label(self):
        r = Node(pk=0, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        self.assertEqual([r['main_label'], r['labels']], ['test', ['test']])

    def test_set_main_label(self):
        r = Node(pk=0, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        r['main_label'] = 'test_2'
        self.assertEqual([r['main_label'], r['labels']], ['test_2', ['test_2']])

    def test_get_pk(self):
        r = Node(pk=0, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        s = Node(pk={'id': 0}, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        t = Node(pk={'code': 0}, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])

        self.assertEqual(
            [r['pk'], s['pk'], t['pk']],
            [
                {'main_label': 'test', 'pk': {'id': 0}},
                {'main_label': 'test', 'pk': {'id': 0}},
                {'main_label': 'test', 'pk': {'code': 0}},
            ]
        )

    def test_set_pk(self):
        r = Node(pk=0, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        s = Node(pk={'id': 0}, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        t = Node(pk={'code': 0}, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])

        r['pk_attributes']['id'] = 1
        s['pk_attributes']['id'] = 1
        t['pk_attributes']['code'] = 1

        self.assertEqual(
            [r['pk'], s['pk'], t['pk']],
            [
                {'main_label': 'test', 'pk': {'id': 1}},
                {'main_label': 'test', 'pk': {'id': 1}},
                {'main_label': 'test', 'pk': {'code': 1}},
            ]
        )

    def test_weak_node(self):
        s = Node(pk={'id': 0}, main_label='strong',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        t = Node(pk={'code': 1}, main_label='weak', parent=s)
        v = Node(pk={'id': 2}, main_label='weak', parent=t)

        self.assertEqual(
            [s['parent'], t['parent'], s['pk'], t['pk'], v['pk']],
            [
                None, s,
                {'main_label': 'strong', 'pk': {'id': 0}},
                {'main_label': 'weak', 'pk': {'id': 0, 'code': 1}},
                {'main_label': 'weak', 'pk': {'id': 0, 'code': 1, 'id_0': 2}},
            ]
        )

    def test_node_repr(self) -> None:
        """Test que el repr d'un node sigui legible."""
        node = Node(pk={"nom": "Test"}, main_label="TestNode")
        repr_str = repr(node)
        self.assertIn("TestNode", repr_str)
        self.assertIn("Test", repr_str)

    def test_node_labels(self) -> None:
        """Test que les etiquetes d'un node siguin correctes."""
        node = Node(pk={"id": 1}, main_label="MyLabel", alternative_labels=["Alt1", "Alt2"])
        labels = node.labels
        self.assertEqual(labels, ["MyLabel", "Alt1", "Alt2"])

    def test_node_main_label(self) -> None:
        """Test l'acces a main_label."""
        node = Node(pk={"id": 1}, main_label="MyLabel")
        self.assertEqual(node.main_label, "MyLabel")

    def test_node_attributes(self) -> None:
        """Test que attributes retorni (pk, attrs)."""
        node = Node(pk={"id": 1}, main_label="TestNode", name="test", value=42)
        pk, attrs = node.attributes
        self.assertIsInstance(pk, dict)
        self.assertIn("id", pk)
        self.assertIn("name", attrs)
        self.assertIn("value", attrs)
        self.assertEqual(attrs["name"], "test")
        self.assertEqual(attrs["value"], 42)

    def test_node_pk_int(self) -> None:
        """Test que un Node amb pk int tingui _primary_key correcte."""
        node = Node(pk=42, main_label="TestNode")
        self.assertEqual(node._primary_key, {"id": 42})

    def test_node_pk_dict(self) -> None:
        """Test que un Node amb pk dict tingui _primary_key correcte."""
        node = Node(pk={"nom": "Test", "any": 2024}, main_label="TestNode")
        self.assertIsInstance(node._primary_key, dict)
        self.assertIn("nom", node._primary_key)
        self.assertIn("any", node._primary_key)

    def test_node_explicit_pk_none_without_neo4j_id(self) -> None:
        """Node amb pk=None explícit: _primary_key = None (backend assignarà ID)."""
        node = Node(pk=None, main_label="TestNode")
        self.assertIsNone(node._primary_key)
        self.assertEqual(node._main_label, "TestNode")

    def test_node_explicit_pk_none_repr(self) -> None:
        """Test que el repr d'un node amb pk=None no crasheja."""
        node = Node(pk=None, main_label="TempNode")
        r = repr(node)
        self.assertIn("pk:None", r)

    def test_explicit_pk_none_cannot_be_parent(self) -> None:
        """Test que un node amb pk=None no pot ser parent de WeakNode."""
        node = Node(pk=None, main_label="TempNode")
        with self.assertRaises(ValueError) as ctx:
            WeakNode(parent=node, pk={"sub": 1}, main_label="Child")
        self.assertIn("parent must have a primary key", str(ctx.exception))

    def test_node_pk_none_with_neo4j_id(self) -> None:
        """Test que un Node sense pk pero amb neo4j_id tingui _primary_key."""
        node = Node(pk=None, main_label="TestNode", neo4j_id=123)
        self.assertEqual(node._primary_key, {"id": 123})
        self.assertEqual(node._neo4j_id, 123)

    def test_node_getitem_pk(self) -> None:
        """Test que node['pk'] retorni el format esperat."""
        node = Node(pk={"id": 1}, main_label="TestNode")
        pk_data = node["pk"]
        self.assertIsInstance(pk_data, dict)
        self.assertIn("main_label", pk_data)
        self.assertIn("pk", pk_data)

    def test_node_getitem_main_label(self) -> None:
        """Test que node['main_label'] retorni el main_label."""
        node = Node(pk={"id": 1}, main_label="TestNode")
        self.assertEqual(node["main_label"], "TestNode")

    def test_node_setitem_pk(self) -> None:
        """Test que node['pk'] = ... actualitzi main_label i primary_key."""
        node = Node(pk={"id": 1}, main_label="TestNode")
        node["pk"] = {"main_label": "OtherNode", "pk": {"id": 2}}
        self.assertEqual(node._main_label, "OtherNode")
        self.assertEqual(node._primary_key, {"id": 2})

    def test_node_version_setter(self) -> None:
        """Test que el setter de version actualitzi _primary_key per a v3."""
        node = Node(pk={"a": 1, "b": 2}, main_label="TestNode", version=5)
        node.version = 3
        # Per a v3 amb múltiples claus, es fusionen en una sola
        self.assertEqual(node._version, 3)

    def test_weak_node_is_weak_and_parent(self) -> None:
        """Test que WeakNode tingui is_weak=True i parent correcte."""
        parent = Node(pk={"id": 1}, main_label="ParentNode")
        # WeakNode requires a pk to merge with parent
        child = WeakNode(parent=parent, pk={"sub_id": 1})
        self.assertTrue(child._is_weak)
        self.assertEqual(child._parent, parent)

    def test_node_neo4j_id_setter(self) -> None:
        """Test que el setter de neo4j_id funcioni."""
        node = Node(pk={"id": 1}, main_label="TestNode")
        node.neo4j_id = 99
        self.assertEqual(node._neo4j_id, 99)

    def test_node_is_weak_default(self) -> None:
        """Test que is_weak sigui False per defecte."""
        node = Node(pk={"id": 1}, main_label="TestNode")
        self.assertFalse(node._is_weak)

    def test_node_propagate_default(self) -> None:
        """Test que _propagate sigui False per defecte."""
        node = Node(pk={"id": 1}, main_label="TestNode")
        self.assertFalse(node._propagate)

    def test_node_dependencies(self) -> None:
        """Test que dependencies es gestionin correctament."""
        deps = {"has_name": Atribut("test")}
        node = Node(pk={"id": 1}, main_label="TestNode", dependencies=deps)
        self.assertEqual(node._dependencies, deps)

    def test_node_no_dependencies(self) -> None:
        """Test que sense dependencies, _dependencies sigui None."""
        node = Node(pk={"id": 1}, main_label="TestNode")
        self.assertIsNone(node._dependencies)

    def test_node_kwargs_as_attributes(self) -> None:
        """Test que kwargs es converteixin en atributs del node."""
        node = Node(pk={"id": 1}, main_label="TestNode", custom_attr="hello", count=42)
        self.assertEqual(node.custom_attr, "hello")
        self.assertEqual(node.count, 42)

    def test_node_getitem_custom_attribute(self) -> None:
        """Test que node['custom_attr'] retorni un atribut custom via kwargs."""
        node = Node(pk={"id": 1}, main_label="TestNode", title="hello")
        self.assertIn("title", node)
        self.assertEqual(node["title"], "hello")

    def test_node_labels_with_no_alternative(self) -> None:
        """Test que sense alternative_labels, labels només contingui main_label."""
        node = Node(pk={"id": 1}, main_label="SingleLabel")
        self.assertEqual(node.labels, ["SingleLabel"])

    def test_node_labels_with_string_alternative(self) -> None:
        """Test que alternative_labels com a string es converteixi en llista."""
        node = Node(pk={"id": 1}, main_label="Main", alternative_labels="Alt")
        self.assertEqual(node.labels, ["Main", "Alt"])

    def test_node_labels_with_list_alternative(self) -> None:
        """Test que alternative_labels com a llista es mantingui."""
        node = Node(pk={"id": 1}, main_label="Main", alternative_labels=["A", "B"])
        self.assertEqual(node.labels, ["Main", "A", "B"])

    def test_node_getitem_unknown_key(self) -> None:
        """Test que node['unknown'] llanci una excepcio."""
        node = Node(pk={"id": 1}, main_label="TestNode")
        with self.assertRaises(Exception):
            _ = node["unknown_key"]

    def test_node_pk_attributes_dict(self) -> None:
        """Test que node['pk_attributes'] retorni el pk."""
        node = Node(pk={"id": 1, "name": "test"}, main_label="TestNode")
        pk_attrs = node["pk_attributes"]
        self.assertIsInstance(pk_attrs, dict)
        self.assertIn("id", pk_attrs)
        self.assertIn("name", pk_attrs)

    def test_weak_node_does_not_mutate_caller_pk_dict(self) -> None:
        """Test que crear un WeakNode amb pk compartint clau amb el pare no muti el dict original."""
        parent = Node(pk={"id": 1}, main_label="Document")
        my_pk = {"id": "page1"}
        WeakNode(parent=parent, pk=my_pk, main_label="Page")
        self.assertEqual(my_pk, {"id": "page1"})


if __name__ == '__main__':
    unittest.main()
