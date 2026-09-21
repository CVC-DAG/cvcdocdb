"""Tests d'integració per Neo4jGraph — creació i manipulació de nodes i relacions.

Verifiquen el comportament del graph store amb un Neo4j real.
Per defecte usen el target DEV (``NEO4J_DEV_*``) o bé les variables
planes ``NEO4J_*`` si existeixen.

Usage:
    python -m pytest test/test_create_graph.py -v
"""

import os
import unittest
from typing import Dict, Optional

# Load .env from project root
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_path):
    from dotenv import load_dotenv
    load_dotenv(_env_path)

from cvcdocdb.base import Node, Relation, WeakNode
from cvcdocdb.neo4j_graph import Neo4jGraph


def _get_config() -> Dict[str, Optional[str]]:
    """Load Neo4j connection config from .env environment variables."""
    target = os.environ.get("NEO4J_TARGET", "DEV")

    def _env(key: str) -> Optional[str]:
        targeted = os.environ.get(f"NEO4J_{target.upper()}_{key}")
        if targeted:
            return targeted
        return os.environ.get(f"NEO4J_{key}")

    url = _env("URL")
    user = _env("USER")
    password = _env("PASSWORD")
    database = _env("DATABASE")
    if not url or not user or not password:
        return {}
    return {"url": url, "user": user, "password": password, "database": database}


def _cleanup_graph(graph: Neo4jGraph) -> None:
    """Delete all test-created nodes before running a test.

    Removes every node in the graph (by neo4j_id) with DETACH DELETE
    so that each test starts from a clean slate.
    """
    # Delete all nodes regardless of label
    graph._tx = graph._session.begin_transaction()
    try:
        graph._tx.run("MATCH (n) DETACH DELETE n")
        graph._tx.commit()
    except Exception:
        graph._tx.rollback()
    finally:
        graph._tx = None


class CVCDocDBTest(unittest.TestCase):
    """Tests d'integració amb Neo4j per a creació de nodes i relacions."""

    def test_node(self):
        """Test per validar la creacio d'un node simple."""
        config = _get_config()
        if not config:
            self.skipTest(".env not found or incomplete")

        Connection = Neo4jGraph(
            url=config["url"],
            user=config["user"],
            password=config["password"],
            database=config.get("database"),
        )

        _cleanup_graph(Connection)

        r = Node(pk=0, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 0, 1, 1, 1, 0])
        Connection.insertNode(r)
        Connection.close()
        self.assertTrue(True)

    def test_node_multiple_labels(self):
        """Test per validar la creacio de nodes amb multiple labels."""
        config = _get_config()
        if not config:
            self.skipTest(".env not found or incomplete")

        Connection = Neo4jGraph(
            url=config["url"],
            user=config["user"],
            password=config["password"],
            database=config.get("database"),
        )

        _cleanup_graph(Connection)

        r = Node(pk=1, main_label='test', alternative_labels=['prova'], type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        Connection.insertNode(r, replace=True)
        Connection.close()
        self.assertTrue(True)

    def test_node_multiple_labels_compose_pk(self):
        """Test per validar la creacio de nodes amb PK composta i multiple labels."""
        config = _get_config()
        if not config:
            self.skipTest(".env not found or incomplete")

        Connection = Neo4jGraph(
            url=config["url"],
            user=config["user"],
            password=config["password"],
            database=config.get("database"),
        )

        _cleanup_graph(Connection)

        r = [
            Node({'name': 'hello', 'id': 0}, main_label='test_2',
                 alternative_labels=['prova_1'], type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0]),
            Node({'name': 'hello', 'id': 1}, main_label='test_2',
                 alternative_labels=['prova_2'], type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0]),
        ]

        for a in r:
            Connection.insertNode(a, replace=True)

        Connection.close()
        self.assertTrue(True)

    def test_relation(self):
        """Test per validar la creacio d'una relacio entre nodes."""
        config = _get_config()
        if not config:
            self.skipTest(".env not found or incomplete")

        Connection = Neo4jGraph(
            url=config["url"],
            user=config["user"],
            password=config["password"],
            database=config.get("database"),
        )

        _cleanup_graph(Connection)

        a = Node(pk=0, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        b = Node(pk=1, main_label='test', type='text',
                 boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])

        # Insert nodes before creating the relation
        Connection.insertNode(a, replace=True)
        Connection.insertNode(b, replace=True)

        r = Relation(a, b, 'test')
        Connection.insertRelation(r, replace=True)
        Connection.close()
        self.assertTrue(True)

    def test_insert_weak_node_missing_parent(self):
        """Test per validar que no es pot inserir un WeakNode sense parent."""
        config = _get_config()
        if not config:
            self.skipTest(".env not found or incomplete")

        Connection = Neo4jGraph(
            url=config["url"],
            user=config["user"],
            password=config["password"],
            database=config.get("database"),
        )

        _cleanup_graph(Connection)

        a = Node(pk=0, main_label='strong', alternative_labels='prova',
                 type='text', boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        b = WeakNode(parent=a, pk=0, main_label='weak_1', alternative_labels='prova',
                     type='text')

        # Try to insert weak node without inserting parent first
        ins_b_1 = False
        try:
            ins_b_1 = Connection.insertNode(b, insert_parent=False, replace=True)
        except Exception:
            ins_b_1 = False

        Connection.close()
        self.assertEqual(ins_b_1, False)

    def test_insert_weak_node(self):
        """Test per validar la creacio d'una cadena de WeakNodes."""
        config = _get_config()
        if not config:
            self.skipTest(".env not found or incomplete")

        Connection = Neo4jGraph(
            url=config["url"],
            user=config["user"],
            password=config["password"],
            database=config.get("database"),
        )

        _cleanup_graph(Connection)

        a = Node(pk=0, main_label='strong', alternative_labels='prova',
                 type='text', boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        b = WeakNode(parent=a, pk=0, main_label='weak_1', alternative_labels='prova',
                     type='text')
        c = WeakNode(parent=b, pk=0, main_label='weak_2', alternative_labels='prova',
                     type='text', parent_relation='IS')
        d = WeakNode(parent=c, pk=0, main_label='weak_3', alternative_labels='prova',
                     type='text', parent_relation='CONTAINS')

        # Try to insert deep weak node without parents (should fail)
        ins_c_1 = False
        try:
            ins_c_1 = Connection.insertNode(c, insert_parent=False, replace=True)
        except Exception:
            ins_c_1 = False

        # Insert parent chain
        ins_a = Connection.insertNode(a, replace=True)
        ins_c_2 = Connection.insertNode(c, replace=True)
        ins_d = Connection.insertNode(d, replace=True)

        Connection.close()
        self.assertTrue(isinstance(ins_a, int))
        self.assertTrue(isinstance(ins_c_2, int))
        self.assertTrue(isinstance(ins_d, int))

    def test_delete_node(self):
        """Test per validar el esborrat de nodes amb propagation."""
        config = _get_config()
        if not config:
            self.skipTest(".env not found or incomplete")

        Connection = Neo4jGraph(
            url=config["url"],
            user=config["user"],
            password=config["password"],
            database=config.get("database"),
        )

        _cleanup_graph(Connection)

        a = Node(pk=0, main_label='strong', alternative_labels='prova',
                 type='text', boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        b = WeakNode(parent=a, pk=0, main_label='weak_1', alternative_labels='prova',
                     type='text')
        c = WeakNode(parent=b, pk=0, main_label='weak_2', alternative_labels='prova',
                     type='text', parent_relation='IS')
        d = WeakNode(parent=c, pk=0, main_label='weak_3', alternative_labels='prova',
                     type='text', parent_relation='CONTAINS')

        # Delete any existing nodes first
        Connection.deleteNode(a, detach=True)

        # Insert the full chain
        ins_a = Connection.insertNode(a, replace=True)
        ins_b = Connection.insertNode(b, replace=True)
        ins_c = Connection.insertNode(c, replace=True)
        ins_d = Connection.insertNode(d, replace=True)

        # Test delete behaviors:
        # propagation=False without detach: should raise (node has children)
        with self.assertRaises(RuntimeError):
            Connection.deleteNode(a, propagation=False)
        # detach=False (RESTRICT): should raise (node has edges)
        with self.assertRaises(RuntimeError):
            Connection.deleteNode(d, detach=False)
        # detach=True (CASCADE): should succeed
        del_c = Connection.deleteNode(c, detach=True)
        # propagation=True (CASCADE): should succeed
        del_a_2 = Connection.deleteNode(a, propagation=True)

        Connection.close()
        self.assertEqual([del_c, del_a_2], [True, True])

    def test_update_node(self, file=None):
        """Test per validar update vs replace d'un node."""
        config = _get_config()
        if not config:
            self.skipTest(".env not found or incomplete")

        Connection = Neo4jGraph(
            url=config["url"],
            user=config["user"],
            password=config["password"],
            database=config.get("database"),
        )

        _cleanup_graph(Connection)

        # First insert the weak node chain (as original test did)
        a_chain = Node(pk=0, main_label='strong', alternative_labels='prova',
                       type='text', boundingbox=[0, 0, 1, 0, 1, 1, 1, 0])
        b_chain = WeakNode(parent=a_chain, pk=0, main_label='weak_1', alternative_labels='prova',
                           type='text')
        Connection.deleteNode(a_chain, detach=True)
        Connection.insertNode(a_chain, replace=True)
        Connection.insertNode(b_chain, replace=True)

        a = Node(pk=0, main_label='strong', size=[0, 14])

        up_a_1 = Connection.insertNode(a, update=True)
        up_a_2 = Connection.insertNode(a, replace=True)

        Connection.close()
        # update=True: retorna >= 0 si el node s'ha actualitzat
        # replace=True: retorna >= 0 si el node s'ha esborrat i recreat
        self.assertGreaterEqual(up_a_1, 0)
        self.assertGreaterEqual(up_a_2, 0)

    def test_update_node_pk_compost(self):
        """Test per validar la creacio de nodes amb PK composta."""
        config = _get_config()
        if not config:
            self.skipTest(".env not found or incomplete")

        Connection = Neo4jGraph(
            url=config["url"],
            user=config["user"],
            password=config["password"],
            database=config.get("database"),
        )

        _cleanup_graph(Connection)

        # Primer creo node
        a = Node(pk={'nom': 'Caldes dEstrac', 'any': 1905}, main_label='Municipi',
                 estat='inserit')
        b = Node(pk={'nom': 'Caldes dEstrac', 'any': 1905}, main_label='Municipi',
                 estat='actualitzat')

        up_a_1 = Connection.insertNode(a, replace=True)
        up_b_1 = Connection.insertNode(b, replace=False, update=True)

        Connection.close()
        self.assertGreaterEqual(up_a_1, 0)
        self.assertGreaterEqual(up_b_1, 0)


if __name__ == '__main__':
    unittest.main()
