"""Neo4jGraph.close() tanca la sessió del driver, no només el driver.

Abans només es tancava el driver: la sessió quedava oberta fins que el
recol·lector d'escombraries la destruïa, i el driver avisava amb un
`ResourceWarning` ("unclosed Session") i un `DeprecationWarning` (les
versions futures del driver ja no tancaran les sessions en destruir-les).

`MemgraphGraph` hereta el mateix `close()`.
"""

from __future__ import annotations

import gc
import os
import unittest
import warnings
from unittest import mock

import pytest

from cvcdocdb import neo4j_graph


def _graph_with_mock_driver():
    with mock.patch.object(neo4j_graph.GraphDatabase, "driver") as driver:
        driver.return_value.get_server_info.return_value.protocol_version = (5, 4)
        graph = neo4j_graph.Neo4jGraph("bolt://example:7687", "u", "p")
    return graph, driver.return_value


class CloseTest(unittest.TestCase):
    def test_close_closes_the_session_before_the_driver(self) -> None:
        graph, driver = _graph_with_mock_driver()
        session = driver.session.return_value
        calls = mock.Mock()
        calls.attach_mock(session.close, "session_close")
        calls.attach_mock(driver.close, "driver_close")
        graph.close()
        self.assertEqual(calls.mock_calls, [mock.call.session_close(), mock.call.driver_close()])

    def test_close_is_idempotent(self) -> None:
        graph, driver = _graph_with_mock_driver()
        graph.close()
        graph.close()
        driver.session.return_value.close.assert_called_once()
        driver.close.assert_called_once()

    def test_open_transaction_is_closed_too(self) -> None:
        graph, driver = _graph_with_mock_driver()
        tx = mock.MagicMock()
        tx.closed.return_value = False
        graph._tx = tx
        graph.close()
        tx.close.assert_called_once()
        driver.session.return_value.close.assert_called_once()


@pytest.mark.slow
class CloseOnRealServerTest(unittest.TestCase):
    def test_no_unclosed_session_warnings(self) -> None:
        url = os.environ.get("NEO4J_DEV_URL")
        if not url:
            self.skipTest("Sense NEO4J_DEV_URL: cal un Neo4j real")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            graph = neo4j_graph.Neo4jGraph(
                url, os.environ.get("NEO4J_DEV_USER", "neo4j"), os.environ.get("NEO4J_DEV_PASSWORD", ""),
                database=os.environ.get("NEO4J_DEV_DATABASE") or None,
            )
            graph.query("RETURN 1 AS x")
            graph.close()
            del graph
            gc.collect()
        messages = [str(w.message) for w in caught]
        self.assertFalse([m for m in messages if "unclosed" in m.lower() or "destructor" in m.lower()], messages)
