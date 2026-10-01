"""Backend Apache Jena (SPARQL): `JenaGraph`.

`JenaGraph` reutilitza la lògica de `NetworkXGraph` (idèntica a la de Neo4j:
ho demostren els escenaris compartits) i desa les dades a Fuseki com a RDF:
cada operació d'escriptura, o un `batch()` sencer, s'envia com UNA petició
SPARQL Update atòmica amb només els canvis, protegida per un control de
versió que detecta escriptures concurrents.

Tests `slow`: necessiten un Fuseki (``FUSEKI_URL``, p. ex.
``http://localhost:3030/ds``; el conftest n'arrenca un amb Docker si cal).
"""

from __future__ import annotations

import os
import unittest
import uuid
import warnings
from typing import Any, Dict, List

import pytest

from cvcdocdb.base import Node, Relation, WeakNode

from test import propagation_scenarios as ps
from test import test_graph_store_contract as _contract
from test.test_propagation_contract import PropagationContractChecks


def _fuseki_url() -> str:
    url = os.environ.get("FUSEKI_URL")
    if not url:
        raise unittest.SkipTest("Sense FUSEKI_URL: cal un Fuseki real")
    return url


def _namespace() -> str:
    # Un espai de noms propi per test: els tests no es trepitgen les dades.
    return f"urn:cvcdocdb-test:{uuid.uuid4().hex}:"


def _fresh_jena(namespace: str = "") -> Any:
    """JenaGraph buit en un espai de noms propi; en tancar-lo esborra les
    seves dades del servidor, perquè els tests no hi deixin restes."""
    from cvcdocdb.jena_graph import JenaGraph

    class _SelfCleaningJenaGraph(JenaGraph):
        def close(self) -> None:
            self.clear()
            super().close()

    graph = _SelfCleaningJenaGraph(_fuseki_url(), namespace=namespace or _namespace())
    graph.clear()
    return graph


def _fresh_networkx() -> Any:
    import tempfile

    from cvcdocdb.networkx_graph import NetworkXGraph

    return NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "g.pkl"))


# ---------------------------------------------------------------------------
# Sense servidor
# ---------------------------------------------------------------------------


class JenaUnitTest(unittest.TestCase):
    def test_exported_lazily_and_is_a_graph_store(self) -> None:
        import cvcdocdb
        from cvcdocdb.graph_store import GraphStore
        from cvcdocdb.jena_graph import JenaGraph

        self.assertIs(cvcdocdb.JenaGraph, JenaGraph)
        self.assertTrue(issubclass(JenaGraph, GraphStore))

    def test_sparql_and_cypher_are_told_apart(self) -> None:
        from cvcdocdb.jena_graph import is_sparql

        for text in (
            "SELECT * WHERE { ?s ?p ?o }",
            "  prefix ex: <urn:x> select ?s where { ?s a ex:T }",
            "# comment\nASK { ?s ?p ?o }",
            "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }",
            "DESCRIBE <urn:x>",
            "BASE <urn:> SELECT ?s WHERE { ?s ?p ?o }",
            "INSERT DATA { <urn:a> <urn:b> <urn:c> }",
            "DELETE WHERE { ?s ?p ?o }",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_sparql(text))
        for text in ("MATCH (n) RETURN n", "CREATE (n:T)", "OPTIONAL MATCH (n) RETURN n", "WITH 1 AS x RETURN x"):
            with self.subTest(text=text):
                self.assertFalse(is_sparql(text))

    def test_unreachable_server_raises_a_clear_error(self) -> None:
        from cvcdocdb.jena_graph import JenaGraph, SparqlError

        with self.assertRaises(SparqlError):
            JenaGraph("http://127.0.0.1:9/ds", timeout=1)


# ---------------------------------------------------------------------------
# Contractes compartits
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestJenaGraphContract(_contract.TestNetworkXGraph):
    """Tots els tests de contracte de NetworkXGraph, sobre Jena."""

    def setUp(self) -> None:
        self._namespace = _namespace()

    def tearDown(self) -> None:
        _fresh_jena(self._namespace).close()

    def _make_graph(self) -> Any:
        from cvcdocdb.jena_graph import JenaGraph

        return JenaGraph(_fuseki_url(), namespace=self._namespace)


@pytest.mark.slow
class JenaPropagationContractTest(PropagationContractChecks, unittest.TestCase):
    make_graph = staticmethod(_fresh_jena)


@pytest.mark.slow
class JenaMatchesNetworkXTest(unittest.TestCase):
    """Cada escenari de propagació deixa el mateix graf i els mateixos errors
    a Jena que a NetworkX (que, al seu torn, és idèntic a Neo4j)."""

    def test_every_scenario_matches(self) -> None:
        _fuseki_url()
        for scenario in ps.SCENARIOS:
            with self.subTest(scenario=scenario.__name__):
                expected = ps.run_scenario(_fresh_networkx, scenario)
                actual = ps.run_scenario(_fresh_jena, scenario)
                self.assertEqual(actual, expected)


# ---------------------------------------------------------------------------
# Específic de Jena
# ---------------------------------------------------------------------------


@pytest.mark.slow
class JenaPersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.namespace = _namespace()
        self.graph = _fresh_jena(self.namespace)

    def tearDown(self) -> None:
        self.graph.clear()
        self.graph.close()

    def reopened(self) -> Any:
        from cvcdocdb.jena_graph import JenaGraph

        return JenaGraph(_fuseki_url(), namespace=self.namespace)

    def test_data_is_stored_on_the_server(self) -> None:
        _, _, page = ps.document_tree()
        self.graph.insertNode(page)
        other = self.reopened()
        try:
            self.assertEqual(ps.snapshot(other), ps.snapshot(self.graph))
            self.assertEqual(len(other.get_node_ids()), 3)
        finally:
            other.close()

    def test_rdf_can_be_queried_with_sparql(self) -> None:
        self.graph.insertNode(Node(pk={"doc": "D1"}, main_label="Document", title="Padró"))
        rows = self.graph.query(
            f"SELECT ?title WHERE {{ ?d a <{self.namespace}label/Document> ; "
            f"<{self.namespace}prop/title> ?title }}"
        )
        self.assertEqual(rows, [{"title": "Padró"}])

    def test_cypher_still_works_on_the_cached_graph(self) -> None:
        self.graph.insertNode(Node(pk={"doc": "D1"}, main_label="Document", title="Padró"))
        self.assertEqual(self.graph.query("MATCH (d:Document) RETURN d.title AS t"), [{"t": "Padró"}])

    def test_cypher_write_queries_are_persisted(self) -> None:
        self.graph.query("CREATE (n:Note {text: 'hola'})")
        other = self.reopened()
        try:
            self.assertEqual(other.query("MATCH (n:Note) RETURN n.text AS t"), [{"t": "hola"}])
        finally:
            other.close()

    def test_sparql_update_through_query_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.graph.query("INSERT DATA { <urn:a> <urn:b> <urn:c> }")

    def test_failed_batch_writes_nothing(self) -> None:
        self.graph.insertNode(Node(pk={"id": 1}, main_label="T"))
        with self.assertRaises(RuntimeError):
            with self.graph.batch():
                self.graph.insertNode(Node(pk={"id": 2}, main_label="T"))
                self.graph.insertNode(Node(pk={"id": 1}, main_label="T"))
        other = self.reopened()
        try:
            self.assertEqual(len(other.get_node_ids()), 1)
        finally:
            other.close()
        self.assertEqual(len(self.graph.get_node_ids()), 1)

    def test_a_batch_is_sent_as_a_single_update(self) -> None:
        sent: List[str] = []
        original = self.graph._client.update
        self.graph._client.update = lambda text: (sent.append(text), original(text))[1]
        with self.graph.batch():
            for i in range(5):
                self.graph.insertNode(Node(pk={"id": i}, main_label="T"))
        self.assertEqual(len(sent), 1)

    def test_concurrent_write_is_detected_and_rolled_back(self) -> None:
        from cvcdocdb.jena_graph import ConcurrentModificationError

        other = self.reopened()
        try:
            with self.assertRaises(ConcurrentModificationError):
                with self.graph.batch():
                    self.graph.insertNode(Node(pk={"id": 1}, main_label="Mine"))
                    other.insertNode(Node(pk={"id": 2}, main_label="Theirs"))
            # La nostra escriptura no s'ha aplicat; la de l'altre client, sí.
            labels = sorted(self.graph.get_node_attrs(n)["main_label"] for n in self.graph.get_node_ids())
            self.assertEqual(labels, ["Theirs"])
            check = self.reopened()
            try:
                self.assertEqual(
                    sorted(check.get_node_attrs(n)["main_label"] for n in check.get_node_ids()), ["Theirs"]
                )
            finally:
                check.close()
        finally:
            other.close()

    def test_changes_from_another_client_are_seen_by_the_next_write(self) -> None:
        other = self.reopened()
        try:
            other.insertNode(Node(pk={"id": 1}, main_label="T"))
        finally:
            other.close()
        self.graph.insertNode(Node(pk={"id": 2}, main_label="T"))
        self.assertEqual(len(self.graph.get_node_ids()), 2)
        with self.assertRaises(RuntimeError):
            self.graph.insertNode(Node(pk={"id": 1}, main_label="T"))  # duplicada

    def test_data_outside_its_namespace_is_left_alone(self) -> None:
        self.graph._client.update("INSERT DATA { <urn:foreign:x> <urn:foreign:p> \"keep\" }")
        try:
            self.graph.insertNode(Node(pk={"id": 1}, main_label="T"))
            self.graph.clear()
            rows = self.graph.query("SELECT ?o WHERE { <urn:foreign:x> <urn:foreign:p> ?o }")
            self.assertEqual(rows, [{"o": "keep"}])
        finally:
            self.graph._client.update("DELETE DATA { <urn:foreign:x> <urn:foreign:p> \"keep\" }")

    def test_vector_indexes_are_not_supported(self) -> None:
        with self.assertRaises(NotImplementedError):
            self.graph.enable_vector_index("embedding", dimensions=3)
        self.assertEqual(self.graph.list_vector_indexes(), [])

    def test_edge_properties_are_readable_as_rdf_reifiers(self) -> None:
        a, b = Node(pk={"id": 1}, main_label="T"), Node(pk={"id": 2}, main_label="T")
        self.graph.insertNode(a)
        self.graph.insertNode(b)
        self.graph.insertRelation(Relation(a, b, "LINKS", weight=3))
        ns = self.namespace
        rows = self.graph.query(
            f"SELECT ?w WHERE {{ ?s <{ns}rel/LINKS> ?o {{| <{ns}prop/weight> ?w |}} }}"
        )
        self.assertEqual(rows, [{"w": 3}])


@pytest.mark.slow
class JenaNamedGraphTest(unittest.TestCase):
    def test_data_can_live_in_a_named_graph(self) -> None:
        from cvcdocdb.jena_graph import JenaGraph

        namespace, graph_iri = _namespace(), f"urn:cvcdocdb-test-graph:{uuid.uuid4().hex}"
        graph = JenaGraph(_fuseki_url(), namespace=namespace, graph_iri=graph_iri)
        try:
            _, _, page = ps.document_tree()
            graph.insertNode(page)
            in_named = graph.query(
                f"SELECT (COUNT(?s) AS ?n) WHERE {{ GRAPH <{graph_iri}> {{ ?s a ?type }} }}"
            )
            in_default = graph.query(f"ASK {{ ?s a <{namespace}label/Document> }}")
            self.assertEqual(in_named, [{"n": 3}])
            self.assertEqual(in_default, [{"ask": False}])
            reopened = JenaGraph(_fuseki_url(), namespace=namespace, graph_iri=graph_iri)
            try:
                self.assertEqual(ps.snapshot(reopened), ps.snapshot(graph))
            finally:
                reopened.close()
            graph.deleteNode(Node(pk={"doc": "D1"}, main_label="Document"), propagation=True, detach=True)
            self.assertEqual(graph.get_node_ids(), [])
        finally:
            graph.clear()
            graph.close()


# ---------------------------------------------------------------------------
# Versió mínima de Fuseki (>= 6.2.0)
# ---------------------------------------------------------------------------


class FusekiVersionTest(unittest.TestCase):
    """JenaGraph exigeix Apache Jena Fuseki >= 6.2.0 (via ``/$/server``)."""

    def _connect(self, info: Any, **kwargs: Any) -> Any:
        from unittest import mock

        from cvcdocdb import jena_graph

        def fake_fetch(url: str, client: Any) -> Any:
            if isinstance(info, Exception):
                raise info
            return info

        loads = []
        with mock.patch.object(jena_graph, "_fetch_server_info", side_effect=fake_fetch), \
                mock.patch.object(jena_graph.JenaGraph, "_load_state", lambda self: loads.append(1)):
            graph = jena_graph.JenaGraph("http://fuseki.example:3030/ds", **kwargs)
        return graph, loads

    def test_minimum_version_is_6_2_0(self) -> None:
        from cvcdocdb.jena_graph import MIN_FUSEKI_VERSION

        self.assertEqual(MIN_FUSEKI_VERSION, (6, 2, 0))

    def test_supported_versions_are_accepted(self) -> None:
        for version in ("6.2.0", "6.2.1", "6.10.0", "7.0.0", "6.3.0-SNAPSHOT"):
            with self.subTest(version=version):
                graph, loads = self._connect({"version": version})
                self.assertEqual(graph.server_version, version)
                self.assertEqual(loads, [1])

    def test_older_versions_are_refused_before_reading_any_data(self) -> None:
        from cvcdocdb.jena_graph import FusekiVersionError

        for version in ("6.1.9", "5.6.0", "4.10.0", "6.2.0-rc1"):
            with self.subTest(version=version):
                with self.assertRaises(FusekiVersionError) as caught:
                    self._connect({"version": version})
                self.assertIn(version, str(caught.exception))
                self.assertIn("6.2.0", str(caught.exception))

    def test_unknown_version_is_refused_by_default(self) -> None:
        from cvcdocdb.jena_graph import FusekiVersionError, SparqlError

        for info in (SparqlError("HTTP 404"), {"name": "not fuseki"}, {"version": "unknown"}):
            with self.subTest(info=info):
                with self.assertRaises(FusekiVersionError) as caught:
                    self._connect(info)
                self.assertIn("check_fuseki_version=False", str(caught.exception))

    def test_check_can_be_disabled_for_other_sparql_stores(self) -> None:
        from cvcdocdb.jena_graph import SparqlError

        graph, loads = self._connect(SparqlError("HTTP 404"), check_fuseki_version=False)
        self.assertIsNone(graph.server_version)
        self.assertEqual(loads, [1])

    def test_server_url_is_derived_from_the_dataset_url(self) -> None:
        from cvcdocdb.jena_graph import _server_info_urls

        self.assertEqual(_server_info_urls("http://h:3030/ds"), ["http://h:3030/$/server"])
        self.assertEqual(
            _server_info_urls("https://h/sparql/ds/"),
            ["https://h/sparql/$/server", "https://h/$/server"],
        )

    def test_explicit_server_url(self) -> None:
        from unittest import mock

        from cvcdocdb import jena_graph

        seen = []
        with mock.patch.object(jena_graph, "_fetch_server_info",
                               side_effect=lambda url, client: seen.append(url) or {"version": "6.2.0"}), \
                mock.patch.object(jena_graph.JenaGraph, "_load_state", lambda self: None):
            jena_graph.JenaGraph("http://proxy/x/ds", server_url="http://fuseki:3030")
        self.assertEqual(seen, ["http://fuseki:3030/$/server"])


@pytest.mark.slow
class FusekiVersionOnRealServerTest(unittest.TestCase):
    def test_real_server_reports_a_supported_version(self) -> None:
        graph = _fresh_jena()
        try:
            self.assertIsNotNone(graph.server_version)
            from cvcdocdb.jena_graph import MIN_FUSEKI_VERSION, _parse_version

            self.assertGreaterEqual(_parse_version(graph.server_version), MIN_FUSEKI_VERSION)
        finally:
            graph.close()
