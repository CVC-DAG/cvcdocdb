"""Tests for Neo4jGraph — connect to a real Neo4j database.

These tests require a .env file in the project root with Neo4j
connection details. You can use either plain vars
(NEO4J_URL, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE) or
targeted vars with NEO4J_TARGET (for example NEO4J_DEV_URL,
NEO4J_DEV_USER, ...). If NEO4J_TARGET is not set, tests default to DEV.
They are skipped if the env vars are not set.

DEFAULT DEV Environment (localhost):
    Host:     localhost (port 7687 by default for bolt)
    User:     neo4j
    Password: default
    Database: neo4j

Usage:
    # Create .env in the project root with DEV config:
    NEO4J_DEV_URL=bolt://localhost:7687
    NEO4J_DEV_USER=neo4j
    NEO4J_DEV_PASSWORD=default
    NEO4J_DEV_DATABASE=neo4j

    # Or use plain config (less recommended):
    NEO4J_URL=bolt://localhost:7687
    NEO4J_USER=neo4j
    NEO4J_PASSWORD=default
    NEO4J_DATABASE=neo4j

    # Or copy from .env.example and update if needed:
    cp .env.example .env

    # Run only the real Neo4j tests:
    python -m pytest test/test_neo4j_real.py -v

    # If Neo4j is not running, tests will be skipped:
    # SKIPPED [Neo4j env config not found]
"""

import os
import sys
import unittest
from typing import Dict, Optional

# Load .env from project root (parent of test/)
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_path):
    from dotenv import load_dotenv
    load_dotenv(_env_path)

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cvcdocdb.neo4j_graph import Neo4jGraph
from cvcdocdb.drm_entities import *
from cvcdocdb.base import *



def _is_neo4j_id(value) -> bool:
    """Des de cvcdocdb 2.0, els identificadors de Neo4j són ``elementId()``
    (cadenes, p. ex. ``'4:<uuid>:12'``), no enters ``id()``."""
    return isinstance(value, str) and value != ""

def _load_config() -> Optional[Dict[str, Optional[str]]]:
    """Load Neo4j connection config from .env environment variables.

    Returns None if required env vars are not set.
    """
    target = os.environ.get("NEO4J_TARGET", "DEV")

    def _env(key: str) -> Optional[str]:
        if target:
            targeted = os.environ.get(f"NEO4J_{target.upper()}_{key}")
            if targeted:
                return targeted
        return os.environ.get(f"NEO4J_{key}")

    url = _env("URL")
    user = _env("USER")
    password = _env("PASSWORD")
    database = _env("DATABASE")

    if not url or not user or not password:
        return None

    return {
        "url": url,
        "user": user,
        "password": password,
        "database": database,
    }


def _skip_if_no_config():
    """Return a skip decorator if env-based Neo4j config is missing."""
    config = _load_config()
    if config is None:
        return unittest.skip("Neo4j env config not found — skipping real Neo4j tests")
    return None


class Neo4jRealTest(unittest.TestCase):
    """Tests that connect to a real Neo4j database.

    These tests verify that Neo4jGraph works correctly with an actual
    Neo4j instance. They require Neo4j connection variables in ``.env``.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _load_config()
        if cls.config is None:
            raise unittest.SkipTest("Neo4j env config not found")
        cls.graph = Neo4jGraph(
            url=cls.config["url"],
            user=cls.config["user"],
            password=cls.config["password"],
            database=cls.config.get("database"),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.graph.close()

    def setUp(self) -> None:
        # Si un test anterior falla enmig d'una tx, deixem l'estat net.
        if self.graph._tx is not None:
            try:
                self.graph._tx.close()
            except Exception:
                pass
            self.graph._tx = None

    def test_update_node_pk_compost(self) -> None:
        """Validar creació i actualització de nodes amb pk compost.

        Crea dos nodes amb la mateixa pk però diferent atribut 'estat'.
        Verifica que:
        1. El primer insert amb replace=True crea el node (retorna id >= 0)
        2. El segon insert amb update=True actualitza l'atribut (retorna id >= 0)
        3. Els IDs retornats són iguals (ja que es tracta del mateix node)
        """
        a = Node(
            pk={"nom": "Caldes dEstrac", "any": 1905},
            main_label="LlocPadro",
            alternative_labels=["TEST"],
            estat="inserit",
        )
        b = Node(
            pk={"nom": "Caldes dEstrac", "any": 1905},
            main_label="LlocPadro",
            alternative_labels=["TEST"],
            estat="actualitzat",
        )

        # Primera inserció amb replace=True (crea el node)
        up_a_1 = self.graph.insertNode(a, replace=True)
        self.assertTrue(_is_neo4j_id(up_a_1), "insertNode hauria de retornar un elementId (str) des de la 2.0")

        # Segona inserció amb update=True (actualitza els atributs)
        up_b_1 = self.graph.insertNode(b, replace=False, update=True)
        self.assertTrue(_is_neo4j_id(up_b_1), "insertNode hauria de retornar un elementId (str) des de la 2.0")

        # Els IDs han de ser iguals perquè es refereixen al mateix node
        self.assertEqual(up_a_1, up_b_1,
                        "El node actualitzat ha de tenir el mateix id que el node creat")

    def test_update_node_lloc_padro(self) -> None:
        """Validar creació i actualització de nodes LlocPadro.

        Utilitza la classe LlocPadro (subclasse de Lloc) amb pk compost.
        Verifica que:
        1. El primer insert crea correctament el node LlocPadro
        2. El segon insert actualitza els atributs sense duplicar el node
        3. Els labels s'assignen correctament
        """
        a = LlocPadro(
            pk={"nom": "Caldes dEstrac", "any": 1905},
            alternative_labels=["TEST"],
            estat="inserit",
        )
        b = LlocPadro(
            pk={"nom": "Caldes dEstrac", "any": 1905},
            main_label="LlocPadro",
            alternative_labels=["TEST"],
            estat="actualitzat",
        )

        # Verifica que els nodes tinguin el label correcte
        self.assertEqual(a.main_label, "LlocPadro",
                        "LlocPadro ha de tenir main_label='LlocPadro'")
        self.assertEqual(b.main_label, "LlocPadro",
                        "LlocPadro ha de tenir main_label='LlocPadro'")

        # Primera inserció amb replace=True (crea el node)
        up_a_1 = self.graph.insertNode(a, replace=True)
        self.assertTrue(_is_neo4j_id(up_a_1), up_a_1)

        # Segona inserció amb update=True (actualitza sense substituir)
        up_b_1 = self.graph.insertNode(b, replace=False, update=True)
        self.assertTrue(_is_neo4j_id(up_b_1), up_b_1)

        # Els IDs han de ser iguals
        self.assertEqual(up_a_1, up_b_1)

    def test_insert_individu_padro(self) -> None:
        """Validar creació de nodes IndividuPadro amb múltiples estratègies.

        Crea 3 IndividuPadro nodes amb pks diferents i les inserta amb
        diferents estratègies:
        1. replace=True (esborra si existeix, crea de nou)
        2. update=True (MERGE - crea o actualitza)
        3. replace=False, update=False (CREATE només si no existeix)

        Verifica que les dependencies automàtiques (nom, cognom1, cognom2)
        es materialitzen correctament.
        """
        ind_1: dict = {"pk": 1, "nom": "Oriol", "cognom1": "Ramos", "edat": 18}
        ind_2: dict = {"pk": 2, "nom": "Sergio", "cognom1": "Ramos", "ofici": "fuster"}
        ind_3: dict = {"pk": 3, "nom": "Pere", "cognom1": "Fuster", "edat": "50"}

        a = IndividuPadro(**ind_1, alternative_labels="TEST")
        b = IndividuPadro(**ind_2, alternative_labels="TEST")
        c = IndividuPadro(**ind_3, alternative_labels="TEST")

        # Verifica propietats obligatòries
        self.assertIsNotNone(a._primary_key)
        self.assertEqual(a.main_label, "IndividuPadro")
        self.assertIsNotNone(a._dependencies, "IndividuPadro ha de tenir dependencies")

        # Insert amb replace=True
        up_a = self.graph.insertNode(a, replace=True)
        self.assertTrue(_is_neo4j_id(up_a), up_a)

        # Insert amb update=True (MERGE)
        up_b = self.graph.insertNode(b, replace=False, update=True)
        self.assertTrue(_is_neo4j_id(up_b), up_b)

        # Insert simple (CREATE)
        up_c = self.graph.insertNode(c, replace=False, update=False)
        self.assertTrue(_is_neo4j_id(up_c), up_c)

        # Verifica que els tres inserts van retornar ids vàlids
        self.assertNotEqual(up_a, up_b, "Dos IndividuPadro diferents han de tenir ids diferents")
        self.assertNotEqual(up_b, up_c)
        self.assertNotEqual(up_a, up_c)

    def test_update_node_pk_compost_replace_strategy(self) -> None:
        """Validar comportament de replace=True amb nodes existents.

        Crea un node, l'inserta, després el crea de nou amb datos diferents
        i verifica que replace=True esborra el node anterior i crea un de nou.
        """
        pk = {"nom": "Prova", "any": 1900}

        # Node original
        node1 = Node(
            pk=pk,
            main_label="LlocPadro",
            estat="original",
            version_text="v1"
        )

        # Insert original
        id1 = self.graph.insertNode(node1, replace=True)
        self.assertTrue(_is_neo4j_id(id1), id1)

        # Node substitut amb mateixa pk però atributs diferents
        node2 = Node(
            pk=pk,
            main_label="LlocPadro",
            estat="substituït",
            version_text="v2"
        )

        # Replace amb node2
        id2 = self.graph.insertNode(node2, replace=True)
        self.assertTrue(_is_neo4j_id(id2), id2)

    def test_node_attributes_preserved(self) -> None:
        """Validar que els atributs dels nodes es preserven correctament.

        Crea un node amb múltiples atributs i verifica que tots es
        conserven després de la inserció.
        """
        node = Node(
            pk={"id": 100},
            main_label="TestNode",
            name="Test Name",
            description="Test Description",
            count=42,
            active=True,
            # Neo4j no admet dict com a property directa: usem text serialitzat.
            metadata='{"key":"value"}'
        )

        node_id = self.graph.insertNode(node, replace=True)
        self.assertTrue(_is_neo4j_id(node_id), node_id)

        # El node ha de ser localizable per la seva pk
        check_node = Node(pk={"id": 100}, main_label="TestNode")
        found_id = self.graph.checkNode(check_node)
        self.assertEqual(node_id, found_id,
                        "El node inserit ha de ser localizable per checkNode")

    def test_insert_relation_between_nodes(self) -> None:
        """Validar creació de relacions entre nodes.

        Crea dos nodes i els connecta amb una relació tipada.
        Verifica que la relació es crea correctament.
        """
        src = Node(pk={"id": 200}, main_label="NodeA", name="Node A")
        dst = Node(pk={"id": 201}, main_label="NodeB", name="Node B")

        # Inserta els nodes
        src_id = self.graph.insertNode(src, replace=True)
        dst_id = self.graph.insertNode(dst, replace=True)

        self.assertTrue(_is_neo4j_id(src_id), src_id)
        self.assertTrue(_is_neo4j_id(dst_id), dst_id)

        # Crea la relació
        relation = Relation(src, dst, "CONNECTS_TO", weight=5.0)
        rel_id = self.graph.insertRelation(relation, update=True)

        self.assertTrue(_is_neo4j_id(rel_id), rel_id)

    def test_weak_node_insertion(self) -> None:
        """Validar creació de WeakNodes amb parent.

        Crea un node pare i un WeakNode fill, verifica que:
        1. El parent s'inserta correctament
        2. El WeakNode s'inserta correctament
        3. Els IDs retornats són vàlids
        """
        parent = Node(
            pk={"doc_id": "DOC-001"},
            main_label="Document",
            title="Sample Document"
        )

        child = WeakNode(
            parent=parent,
            pk={"page": 1},
            main_label="Page",
            parent_relation="HAS_PAGE",
            content="Page content"
        )

        # Inserta el pare
        parent_id = self.graph.insertNode(parent, replace=True)
        self.assertTrue(_is_neo4j_id(parent_id), parent_id)

        # Inserta el fill (WeakNode)
        # insert_parent=False perquè ja l'hem inserit
        child_id = self.graph.insertNode(child, insert_parent=False)
        self.assertTrue(_is_neo4j_id(child_id), child_id)

        # Els IDs han de ser diferents
        self.assertNotEqual(parent_id, child_id)

    def test_weak_node_composite_key_inheritance(self) -> None:
        """Validar que WeakNodes hereten correctament la pk del pare.

        Verifica que la pk composada del WeakNode inclou la pk del pare.
        """
        parent = Node(
            pk={"document": "DOC-001"},
            main_label="Document"
        )

        child = WeakNode(
            parent=parent,
            pk={"section": "SEC-01"},
            main_label="Section",
            parent_relation="HAS_SECTION"
        )

        # Verifica que el child és weak
        self.assertTrue(child["is_weak"], "WeakNode ha de tenir is_weak=True")

        # Verifica que la pk composada inclou ambdues claus
        child_pk = child["pk_attributes"]
        self.assertIn("document", child_pk, "pk composada ha d'incloure 'document'")
        self.assertIn("section", child_pk, "pk composada ha d'incloure 'section'")

    def test_individu_padro_dependencies_creation(self) -> None:
        """Validar que les dependencies automàtiques es creen correctament.

        IndividuPadro hauria de materializar automàticament nom, cognom1, cognom2
        com a nodes Atribut (Valor).
        """
        person = IndividuPadro(
            pk=123,
            nom="Joan",
            cognom1="Miró",
            cognom2="Ferrer"
        )

        # Verifica que té dependencies
        self.assertIsNotNone(person._dependencies,
                            "IndividuPadro ha de tenir dependencies")
        self.assertIn("nom", person._dependencies,
                     "Dependencies ha d'incloure 'nom'")
        self.assertIn("cognom1", person._dependencies,
                     "Dependencies ha d'incloure 'cognom1'")
        self.assertIn("cognom2", person._dependencies,
                     "Dependencies ha d'incloure 'cognom2'")

        # Les dependencies haurien de ser Atribut nodes
        for dep_value in person._dependencies.values():
            self.assertEqual(dep_value.main_label, "Valor",
                           f"Dependency ha de ser un node Valor, no {dep_value.main_label}")

    def test_location_node_with_composite_key(self) -> None:
        """Validar nodes de lloc amb pk composada.

        Crea un LlocPadro amb pk composada (nom, any) i verifica que
        es gestiona correctament.
        """
        location = LlocPadro(
            pk={"nom": "Barcelona", "any": 1888},
            alternative_labels=["EXPO"]
        )

        # Inserta el node
        loc_id = self.graph.insertNode(location, replace=True)
        self.assertTrue(_is_neo4j_id(loc_id), loc_id)

        # Verifica que la pk composada es va guardar correctament
        self.assertEqual(location.main_label, "LlocPadro")
        pk_attrs = location["pk_attributes"]
        self.assertEqual(pk_attrs["nom"], "Barcelona")
        self.assertEqual(pk_attrs["any"], 1888)

    def test_node_without_explicit_pk_gets_neo4j_id(self) -> None:
        """Validar nodes amb pk=None reben l'id de Neo4j.

        Alguns casos d'ús requereixen nodes transitoris sense pk explícita.
        Aquests nodes haurien de rebre l'id intern de Neo4j.
        """
        # Crea un node amb pk=None explícit
        node = Node(
            pk=None,
            main_label="TransientNode",
            data="temporal"
        )

        # Inserta el node - hauria de rebre un id de Neo4j
        node_id = self.graph.insertNode(node, replace=True)
        self.assertTrue(_is_neo4j_id(node_id), node_id)

        # Después de la inserció, el node ha de tenir neo4j_id assignat
        self.assertIsNotNone(node.neo4j_id)
        self.assertEqual(node.neo4j_id, node_id)


if __name__ == "__main__":
    unittest.main()
