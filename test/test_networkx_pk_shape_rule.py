"""NetworkXGraph: una sola forma de pk per etiqueta (2.0, `major_release`).

Mateixa regla i mateix paràmetre que `Neo4jGraph(pk_constraints=True)`,
perquè els dos backends es comportin igual (la promesa de cvcdocdb: el
mateix codi funciona amb qualsevol backend). Sense restriccions a la base,
la forma de cada etiqueta es desa al propi .pkl, així la regla val entre
instàncies i processos. `pk_constraints=False` = comportament 1.x.
"""

from __future__ import annotations

import os
import pickle
import tempfile
import unittest

from cvcdocdb.base import Node, WeakNode
from cvcdocdb.migration import migrate
from cvcdocdb.networkx_graph import NetworkXGraph


class NetworkXPkShapeRuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.path = os.path.join(tempfile.mkdtemp(), "g.pkl")

    def _graph(self, **kwargs) -> NetworkXGraph:
        return NetworkXGraph(persistence_path=self.path, **kwargs)

    def test_second_pk_shape_for_a_label_is_rejected(self) -> None:
        g = self._graph()
        g.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        with self.assertRaises(ValueError) as ctx:
            g.insertNode(Node(pk={"niu": "1234567"}, main_label="User"))
        self.assertIn("email", str(ctx.exception))
        self.assertIn("niu", str(ctx.exception))
        self.assertEqual(len(g.query({"main_label": "User"})), 1)
        g.close()

    def test_same_shape_keeps_working_including_update_and_replace(self) -> None:
        g = self._graph()
        g.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User", nom="A"))
        g.insertNode(Node(pk={"email": "b@uab.cat"}, main_label="User"))
        g.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User", nom="A2"), update=True)
        g.insertNode(Node(pk={"email": "b@uab.cat"}, main_label="User"), replace=True)
        self.assertEqual(len(g.query({"main_label": "User"})), 2)
        g.close()

    def test_rule_holds_across_instances(self) -> None:
        """La forma es desa al .pkl: una altra instància (o procés) la veu."""
        a = self._graph()
        a.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        a.close()
        b = self._graph()
        with self.assertRaises(ValueError):
            b.insertNode(Node(pk={"niu": "1234567"}, main_label="User"))
        b.close()

    def test_node_without_pk_rejected_on_a_label_with_a_shape(self) -> None:
        """Mateixa semàntica que Neo4j: sense pk també és "una altra forma"."""
        g = self._graph()
        g.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        with self.assertRaises(ValueError):
            g.insertNode(Node(pk=None, main_label="User"))
        g.close()

    def test_labels_with_only_backend_assigned_ids_are_allowed(self) -> None:
        g = self._graph()
        g.insertNode(Node(pk=None, main_label="Note"))
        g.insertNode(Node(pk=None, main_label="Note"))
        self.assertEqual(len(g.query({"main_label": "Note"})), 2)
        g.close()

    def test_weak_nodes_follow_the_rule_of_their_own_label(self) -> None:
        g = self._graph()
        doc = Node(pk={"doc_id": "d1"}, main_label="Document")
        g.insertNode(WeakNode(parent=doc, pk={"section": "intro"}, main_label="Section"), insert_parent=True)
        g.insertNode(WeakNode(parent=doc, pk={"section": "body"}, main_label="Section"), insert_parent=True)
        self.assertEqual(len(g.query({"main_label": "Section"})), 2)
        g.close()

    def test_pk_constraints_false_restores_1x_behaviour(self) -> None:
        g = self._graph(pk_constraints=False)
        g.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        g.insertNode(Node(pk={"niu": "1234567"}, main_label="User"))
        self.assertEqual(len(g.query({"main_label": "User"})), 2)
        g.close()

    def test_legacy_pickle_without_saved_shapes_is_rebuilt_from_nodes(self) -> None:
        """Un .pkl de la 1.x no té les formes desades: es reconstrueixen dels
        nodes (sense comptar els identificadors assignats pel backend)."""
        legacy = self._graph(pk_constraints=False)
        legacy.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        legacy.insertNode(Node(pk=None, main_label="Note"))
        legacy.close()
        with open(self.path, "rb") as fh:
            state = pickle.load(fh)
        state.pop("label_pk_shapes", None)  # com un .pkl 1.x
        with open(self.path, "wb") as fh:
            pickle.dump(state, fh)

        g = self._graph()
        with self.assertRaises(ValueError):
            g.insertNode(Node(pk={"niu": "1234567"}, main_label="User"))
        g.insertNode(Node(pk=None, main_label="Note"))  # Note continua sense forma
        g.close()

    def test_migrate_into_networkx_applies_the_rule(self) -> None:
        source = NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "s.pkl"), pk_constraints=False)
        source.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User"))
        source.insertNode(Node(pk={"niu": "1234567"}, main_label="User"))
        target = self._graph()
        stats = migrate(source, target, on_error="skip")
        self.assertEqual(stats.nodes_migrated, 1)
        self.assertEqual(stats.nodes_skipped, 1)
        self.assertTrue(any("pk" in e for e in stats.errors))
        source.close()
        target.close()


if __name__ == "__main__":
    unittest.main()
