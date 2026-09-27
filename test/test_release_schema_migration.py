"""Release-only tests: cross-backend migration + schema codegen.

These exercise cvcdocdb.migration.migrate() and cvcdocdb.schema_gen
end to end against a real Neo4j, and are expensive relative to the
rest of the suite — they're marked "release" and skipped by default
(see test/README.md). Run explicitly with::

    CVCDOCDB_RUN_RELEASE_TESTS=1 pytest test/test_release_schema_migration.py -v -m release
"""

from __future__ import annotations

import os
import re
import tempfile

import pytest

try:
    import yaml
except ImportError:
    yaml = None

from cvcdocdb import NetworkXGraph, Node, Relation
from cvcdocdb.drm_entities import Fons, IndividuPadro, LlocPadro, RegioFisica
from cvcdocdb.exemples.networkx_karate import load_karate_club
from cvcdocdb.migration import migrate
from cvcdocdb.schema_gen import generate_classes

pytestmark = [pytest.mark.release, pytest.mark.slow]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _neo4j_config():
    target = os.environ.get("NEO4J_TARGET", "DEV")

    def _env(key):
        return os.environ.get(f"NEO4J_{target.upper()}_{key}") or os.environ.get(f"NEO4J_{key}")

    url, user, password = _env("URL"), _env("USER"), _env("PASSWORD")
    if not url or not user or not password:
        return None
    return {"url": url, "user": user, "password": password, "database": _env("DATABASE") or "neo4j"}


@pytest.fixture()
def neo4j_store():
    config = _neo4j_config()
    if config is None:
        pytest.skip("NEO4J_DEV_* (or compatible NEO4J target/plain vars) not set")
    from cvcdocdb.neo4j_graph import Neo4jGraph

    graph = Neo4jGraph(url=config["url"], user=config["user"],
                        password=config["password"], database=config["database"])

    def _wipe():
        graph._tx = graph._session.begin_transaction()
        graph._tx.run("MATCH (n) DETACH DELETE n")
        graph._tx.commit()
        graph._tx = None

    _wipe()
    yield graph
    _wipe()
    graph.close()


@pytest.fixture()
def networkx_store():
    path = tempfile.mktemp(suffix=".pkl")
    store = NetworkXGraph(path)
    yield store
    store.close()
    if os.path.exists(path):
        os.remove(path)


def class_names(code: str):
    return sorted(re.findall(r"^class (\w+)", code, re.MULTILINE))


def class_body(code: str, name: str) -> str:
    match = re.search(rf"^class {name}\(.*?(?=^class |\Z)", code, re.MULTILINE | re.DOTALL)
    assert match, f"class {name} not found in generated code:\n{code}"
    return match.group(0)


def build_drm_dataset(store) -> None:
    """A small dataset exercising WeakNode (RegioFisica under Fons),
    be_value_properties (IndividuPadro.nom/cognom1 -> Atribut/Valor
    nodes, shared across individuals with the same first name), and a
    plain Relation (VIU_A)."""
    fons = Fons(pk={"id": 1}, nom="Fons Exemple")
    store.insertNode(fons, replace=True)

    regio = RegioFisica(parent=fons, pk={"region": "A"}, contours="0,0,1,1")
    store.insertNode(regio, insert_parent=True)

    ind1 = IndividuPadro(pk={"id": 1}, nom="Oriol", cognom1="Ramos")
    ind2 = IndividuPadro(pk={"id": 2}, nom="Oriol", cognom1="Vila")
    store.insertNode(ind1)
    store.insertNode(ind2)

    lloc = LlocPadro(pk={"nom": "Caldes"})
    store.insertNode(lloc, replace=True)

    store.insertRelation(Relation(ind1, lloc, "VIU_A"))
    store.insertRelation(Relation(ind2, lloc, "VIU_A"))


_DRM_CORE_CLASSES = {
    "Fons", "IndividuPadro", "LlocPadro", "RegioFisica", "Valor",
    "Nom", "Cognom1", "ViuA", "Has",
}


# ---------------------------------------------------------------------------
# Test 1: drm_entities -> NetworkXGraph -> generate_classes
# ---------------------------------------------------------------------------

class TestDrmEntitiesSchemaNetworkX:
    def test_generated_classes_match_the_persisted_shape(self, networkx_store):
        """NetworkX groups purely by main_label, so the generated classes
        are exactly the "core" drm_entities classes actually used here —
        no more, no less."""
        if yaml is None:
            pytest.skip("PyYAML not installed")
        build_drm_dataset(networkx_store)
        code = generate_classes(networkx_store.schema_yaml("drm_nx"))

        assert set(class_names(code)) == _DRM_CORE_CLASSES

    def test_individupadro_has_no_nom_cognom_properties(self, networkx_store):
        """nom/cognom1 are be_value_properties: at insert time they're
        diverted into separate Atribut/Valor nodes (see Node.__init__ in
        base.py), never stored as plain properties on IndividuPadro
        itself — the introspected class must reflect that, exactly like
        the real hand-written IndividuPadro in drm_entities.py (which
        declares them as constructor kwargs, not as stored attributes)."""
        if yaml is None:
            pytest.skip("PyYAML not installed")
        build_drm_dataset(networkx_store)
        code = generate_classes(networkx_store.schema_yaml("drm_nx"))
        body = class_body(code, "IndividuPadro")

        assert "id: Optional[int]" in body
        assert "nom" not in body
        assert "cognom1" not in body

    def test_valor_node_and_dependency_relations_are_generated(self, networkx_store):
        """The Atribut/Valor materialisation of be_value_properties must
        show up as its own Valor node class plus NOM/COGNOM1 relation
        classes — this is the mechanism Node.get_attrs() relies on to
        hydrate them back (see base.py)."""
        if yaml is None:
            pytest.skip("PyYAML not installed")
        build_drm_dataset(networkx_store)
        code = generate_classes(networkx_store.schema_yaml("drm_nx"))

        valor_body = class_body(code, "Valor")
        assert "name: Optional[str]" in valor_body
        assert "class Nom(Relation)" in code
        assert "class Cognom1(Relation)" in code

    def test_regiofisica_generated_as_weaknode_with_fons_parent(self, networkx_store):
        """RegioFisica(Layout(WeakNode)) must round-trip as a WeakNode
        subclass with Fons as its detected parent and HAS as the
        propagating WeakRelation — this is exactly the cascade-delete
        mechanism WeakNode/WeakRelation exist for."""
        if yaml is None:
            pytest.skip("PyYAML not installed")
        build_drm_dataset(networkx_store)
        code = generate_classes(networkx_store.schema_yaml("drm_nx"))

        regio_body = class_body(code, "RegioFisica")
        assert "class RegioFisica(WeakNode)" in regio_body
        assert "parent: The parent Fons instance." in regio_body
        assert "class Has(WeakRelation)" in code
        assert "propagate=True" in class_body(code, "Has")


# ---------------------------------------------------------------------------
# Test 2: drm_entities -> Neo4jGraph -> generate_classes
# ---------------------------------------------------------------------------

class TestDrmEntitiesSchemaNeo4j:
    def test_core_classes_are_all_present(self, neo4j_store):
        """Every class NetworkX derives from main_label alone must also
        appear from Neo4j's schema (Neo4j's db.labels() sees strictly
        more labels — see the next test — but never fewer)."""
        if yaml is None:
            pytest.skip("PyYAML not installed")
        build_drm_dataset(neo4j_store)
        code = generate_classes(neo4j_store.schema_yaml("drm_neo4j"))

        assert _DRM_CORE_CLASSES <= set(class_names(code))

    def test_alternative_labels_surface_as_extra_classes(self, neo4j_store):
        """KNOWN, EXPECTED divergence from the NetworkX-derived schema:
        drm_entities classes append a Cypher label for each Python base
        class (Individu -> "Individu", Lloc -> "Lloc", Layout ->
        "layout", DocumentCultural -> "DocumentCultural" — see
        _build_alternative_labels in drm_entities.py). Neo4j actually
        stores all of them as real labels on the node and
        `CALL db.labels()` enumerates every one, so Neo4jGraph.schema_yaml()
        (which has no way to tell "primary" from "alternative" purely
        from Neo4j's flat label list — the same fundamental limitation
        as pk detection, see migrate()'s docstring) generates a class
        for each. NetworkXGraph's schema_yaml() only ever groups by
        main_label, so it never sees these.

        This test exists so that if the label model changes, it fails
        loudly instead of the divergence silently drifting further.
        """
        if yaml is None:
            pytest.skip("PyYAML not installed")
        build_drm_dataset(neo4j_store)
        code = generate_classes(neo4j_store.schema_yaml("drm_neo4j"))

        extra = set(class_names(code)) - _DRM_CORE_CLASSES
        assert extra == {"DocumentCultural", "Individu", "Lloc", "Layout"}

    def test_individupadro_has_no_nom_cognom_properties(self, neo4j_store):
        """Same be_value_properties check as the NetworkX test — must
        hold on Neo4j too, since Node.__init__'s dependency
        materialisation is backend-agnostic."""
        if yaml is None:
            pytest.skip("PyYAML not installed")
        build_drm_dataset(neo4j_store)
        code = generate_classes(neo4j_store.schema_yaml("drm_neo4j"))
        body = class_body(code, "IndividuPadro")

        assert "id: Optional[int]" in body
        assert "nom" not in body
        assert "cognom1" not in body


# ---------------------------------------------------------------------------
# Test 3: cross-backend migration (Karate Club) — generated code must match
# ---------------------------------------------------------------------------

class TestKarateClubMigrationSchemaParity:
    def test_generated_classes_match_after_migration(self, networkx_store, neo4j_store):
        """The Karate Club dataset uses plain Node() instances with no
        Python-level inheritance/alternative_labels, so — unlike the
        drm_entities case above — the two backends' schemas (and
        therefore their generated classes) must match exactly after a
        round trip through migrate()."""
        if yaml is None:
            pytest.skip("PyYAML not installed")
        load_karate_club(networkx_store)
        code_before = generate_classes(networkx_store.schema_yaml("karate_nx"))

        stats = migrate(networkx_store, neo4j_store)
        assert stats.nodes_migrated == 36
        assert stats.edges_migrated == 112

        code_after = generate_classes(neo4j_store.schema_yaml("karate_neo4j"))

        assert class_names(code_before) == class_names(code_after)

    def test_interacts_weight_property_survives_migration(self, networkx_store, neo4j_store):
        """Regression test for the schema_yaml() edge-property bugs fixed
        alongside this test: INTERACTS.weight must show up in the
        introspected schema on both sides, not just one."""
        if yaml is None:
            pytest.skip("PyYAML not installed")
        load_karate_club(networkx_store)
        migrate(networkx_store, neo4j_store)

        data_nx = yaml.safe_load(networkx_store.schema_yaml("karate_nx"))
        data_neo4j = yaml.safe_load(neo4j_store.schema_yaml("karate_neo4j"))

        assert "weight" in data_nx["relationships"]["INTERACTS"]["properties"]
        assert "weight" in data_neo4j["relationships"]["INTERACTS"]["properties"]
