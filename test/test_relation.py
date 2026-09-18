"""Tests unitaris per la classe Relation.

Verifiquen el comportament de src/dst dins les relacions i la
propagació de canvis als pk_attributes dels nodes.

Usage:
    python -m pytest test/test_relation.py -v
"""

import unittest

from cvcdocdb.base import Relation, Node


class RelationTest(unittest.TestCase):
    def test_get_node_pks(self):
        a = Node(pk=0, main_label='test_1', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        b = Node(pk={'id': 1}, main_label='test_2', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])

        r = Relation(a, b, 'test_r')

        self.assertEqual([r['src'], r['dst']],
                         [a['pk'], b['pk']])

    def test_set_node_pks_from_rel(self):
        a = Node(pk=0, main_label='test_1', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        b = Node(pk={'id': 1}, main_label='test_2', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])

        r = Relation(a, b, 'test_r')

        r['src']['pk']['id'] = 1
        r['dst']['pk']['id'] = 1

        self.assertEqual([r['src'], r['dst']],
                         [a['pk'], b['pk']])

    def test_set_node_pks_from_node(self):
        a = Node(pk=0, main_label='test_1', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        b = Node(pk={'id': 1}, main_label='test_2', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])

        r = Relation(a, b, 'test_r')

        a['pk_attributes']['id'] = 1
        b['pk_attributes']['id'] = 1

        self.assertEqual([r['src'], r['dst']],
                         [a['pk'], b['pk']])


    def test_relation_src_dst_access(self) -> None:
        """Test que rel["src"] i rel["dst"] retornen el format esperat."""
        src = Node(pk={"id": 1}, main_label="TestNode")
        dst = Node(pk={"id": 2}, main_label="TestNode")
        rel = Relation(src, dst, "FOLLOWS")

        src_data = rel["src"]
        dst_data = rel["dst"]

        self.assertIsInstance(src_data, dict)
        self.assertIn("main_label", src_data)
        self.assertIn("pk", src_data)
        self.assertIsInstance(dst_data, dict)
        self.assertIn("main_label", dst_data)
        self.assertIn("pk", dst_data)

    def test_relation_type_uppercase(self) -> None:
        """Test que el tipus de relacio es converteix a uppercase."""
        src = Node(pk={"id": 1}, main_label="TestNode")
        dst = Node(pk={"id": 2}, main_label="TestNode")
        rel = Relation(src, dst, "lowercase")

        self.assertEqual(rel["type"], "LOWERCASE")

    def test_relation_init(self) -> None:
        """Test que Relation inicialitzi correctament."""
        src = Node(pk={"id": 1}, main_label="SrcNode")
        dst = Node(pk={"id": 2}, main_label="DstNode")
        rel = Relation(src, dst, "CONNECTS")

        self.assertEqual(rel._type, "CONNECTS")
        self.assertIsInstance(rel._src, dict)
        self.assertIsInstance(rel._dst, dict)

    def test_relation_setitem(self) -> None:
        """Test que rel['src'] = ... actualitzi correctament."""
        src = Node(pk={"id": 1}, main_label="SrcNode")
        dst = Node(pk={"id": 2}, main_label="DstNode")
        rel = Relation(src, dst, "CONNECTS")

        new_src = Node(pk={"id": 3}, main_label="NewSrcNode")
        rel["src"] = new_src["pk"]
        self.assertEqual(rel._src["main_label"], "NewSrcNode")

    def test_relation_repr(self) -> None:
        """Test que el repr d'una relacio sigui legible."""
        src = Node(pk={"id": 1}, main_label="SrcNode")
        dst = Node(pk={"id": 2}, main_label="DstNode")
        rel = Relation(src, dst, "CONNECTS")
        repr_str = repr(rel)
        self.assertIn("src:", repr_str)
        self.assertIn("dst:", repr_str)
        self.assertIn("CONNECTS", repr_str)

    def test_relation_getitem_type(self) -> None:
        """Test que rel['type'] retorni el tipus."""
        src = Node(pk={"id": 1}, main_label="SrcNode")
        dst = Node(pk={"id": 2}, main_label="DstNode")
        rel = Relation(src, dst, "TYPE1")
        self.assertEqual(rel["type"], "TYPE1")

    def test_relation_getitem_attributes_empty(self) -> None:
        """Test que rel['attributes'] retorni None si no hi ha atributs."""
        src = Node(pk={"id": 1}, main_label="SrcNode")
        dst = Node(pk={"id": 2}, main_label="DstNode")
        rel = Relation(src, dst, "TYPE1")
        self.assertIsNone(rel["attributes"])

    def test_relation_getitem_attributes_with_data(self) -> None:
        """Test que rel['attributes'] retorni el dict si hi ha atributs."""
        src = Node(pk={"id": 1}, main_label="SrcNode")
        dst = Node(pk={"id": 2}, main_label="DstNode")
        rel = Relation(src, dst, "TYPE1")
        rel["custom"] = "value"
        attrs = rel["attributes"]
        self.assertIsInstance(attrs, dict)
        self.assertIn("custom", attrs)

    def test_relation_getitem_unknown_key(self) -> None:
        """Test que rel['unknown'] llanci una excepcio."""
        src = Node(pk={"id": 1}, main_label="SrcNode")
        dst = Node(pk={"id": 2}, main_label="DstNode")
        rel = Relation(src, dst, "TYPE1")
        with self.assertRaises(Exception):
            _ = rel["unknown_key"]


if __name__ == '__main__':
    unittest.main()
