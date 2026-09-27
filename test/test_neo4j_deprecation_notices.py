"""Neo4jGraph no inunda el log amb avisos de `id()` obsolet.

cvcdocdb fa servir `id()` a moltes consultes (identificadors enters a l'API
pública; `elementId()` retorna cadenes i canviar-ho és trencador, veure
`major_release`). Neo4j 5 marca `id()` com a obsolet i el driver en registra
un avís per consulta: una migració en generava ~480, que tapaven els errors
de debò al log de producció.

La sessió pròpia de Neo4jGraph filtra la categoria DEPRECATION (només
aquesta: la resta d'avisos, p. ex. propietats inexistents, continuen), si
el servidor ho admet (Bolt 5.2+ / Neo4j 5.7+) i si qui crida no ha passat
cap opció `notifications_*` pròpia.
"""

from __future__ import annotations

import logging
import os
import unittest

import pytest

from cvcdocdb.base import Node


class DeprecationFilterDecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        from cvcdocdb.neo4j_graph import _deprecation_filter_session_kwargs

        self.decide = _deprecation_filter_session_kwargs

    def test_filters_deprecation_on_supported_servers(self) -> None:
        kwargs = self.decide((5, 4), {})
        self.assertEqual(len(kwargs), 1)
        (key, value), = kwargs.items()
        self.assertIn(key, ("notifications_disabled_classifications", "notifications_disabled_categories"))
        self.assertEqual([str(getattr(v, "value", v)) for v in value], ["DEPRECATION"])

    def test_no_filter_on_servers_without_notification_filters(self) -> None:
        self.assertEqual(self.decide((5, 1), {}), {})
        self.assertEqual(self.decide((4, 4), {}), {})

    def test_callers_own_notification_settings_are_respected(self) -> None:
        self.assertEqual(self.decide((5, 4), {"notifications_min_severity": "OFF"}), {})
        self.assertEqual(self.decide((5, 4), {"notifications_disabled_classifications": []}), {})


@pytest.mark.slow
class Neo4jDeprecationNoticesTest(unittest.TestCase):
    def setUp(self) -> None:
        url = os.environ.get("NEO4J_DEV_URL")
        if not url:
            self.skipTest("Sense NEO4J_DEV_URL: cal un Neo4j real")
        from cvcdocdb.neo4j_graph import Neo4jGraph

        self.graph = Neo4jGraph(
            url, os.environ.get("NEO4J_DEV_USER", "neo4j"), os.environ.get("NEO4J_DEV_PASSWORD", ""),
            database=os.environ.get("NEO4J_DEV_DATABASE") or None,
        )
        self.graph.query("MATCH (n) DETACH DELETE n")
        self.records: list = []
        handler = logging.Handler()
        handler.emit = self.records.append  # type: ignore[method-assign]
        self.logger = logging.getLogger("neo4j.notifications")
        self.logger.addHandler(handler)
        self.handler = handler

    def tearDown(self) -> None:
        if hasattr(self, "graph"):
            self.logger.removeHandler(self.handler)
            self.graph.query("MATCH (n) DETACH DELETE n")
            self.graph.close()

    def _messages(self) -> list:
        return [r.getMessage() for r in self.records]

    def test_id_deprecation_notices_are_filtered(self) -> None:
        a = Node(pk={"email": "a@uab.cat"}, main_label="User")
        self.graph.insertNode(a)
        self.graph.insertNode(Node(pk={"email": "a@uab.cat"}, main_label="User", nom="A"), update=True)
        self.graph.query({"main_label": "User"})
        self.assertFalse([m for m in self._messages() if "deprecated" in m.lower()], self._messages()[:3])

    def test_other_notifications_still_reach_the_log(self) -> None:
        """Només es filtra DEPRECATION: un avís útil com una propietat que no
        existeix continua arribant."""
        self.graph.query("MATCH (n:User) WHERE n.propietat_que_no_existeix = 1 RETURN n")
        self.assertTrue([m for m in self._messages() if "does not exist" in m], self._messages()[:3])
