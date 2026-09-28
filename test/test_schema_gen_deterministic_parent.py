"""`NetworkXGraph.schema_yaml()` ha de detectar el pare d'un WeakNode de
manera determinista.

El pare d'una etiqueta dèbil és l'etiqueta la pk de la qual és un
subconjunt propi de la seva, amb la coincidència més gran. Quan dos
candidats empataven, guanyava el primer que sortia en recórrer un `set()`
de noms — un ordre que canvia a cada procés (PYTHONHASHSEED) — i el codi
generat canviava d'una execució a una altra (test_release_schema_migration
fallava ~la meitat de les vegades, també a la 1.3.0).

Ara l'empat es desfà amb la prova real — el candidat que té una aresta amb
`_propagate` cap a l'etiqueta filla — i, com a últim recurs, per ordre
alfabètic.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_BUILD_AND_DUMP = textwrap.dedent('''
    import sys, tempfile, os
    from cvcdocdb.base import Node, Relation
    from cvcdocdb.networkx_graph import NetworkXGraph
    g = NetworkXGraph(persistence_path=os.path.join(tempfile.mkdtemp(), "g.pkl"), **KWARGS)
    alpha = Node(pk={"a": 1}, main_label="Alpha")
    zeta = Node(pk={"z": 1}, main_label="Zeta")
    child = Node(pk={"a": 1, "z": 1, "c": 1}, main_label="Child")
    for n in (alpha, zeta, child):
        g.insertNode(n)
    g.insertRelation(Relation(alpha, child, "LINKS"))
    g.insertRelation(Relation(zeta, child, "HAS", _propagate=True))
    sys.stdout.write(g.schema_yaml("test"))
    g.close()
''')


def _schema_yaml(hash_seed: str) -> str:
    """schema_yaml() en un procés nou amb una llavor de hash concreta."""
    import cvcdocdb.networkx_graph as nx_mod
    import inspect

    kwargs = "{'pk_constraints': False}" if "pk_constraints" in inspect.signature(
        nx_mod.NetworkXGraph.__init__).parameters else "{}"
    env = {**os.environ, "PYTHONHASHSEED": hash_seed,
           "PYTHONPATH": str(Path(nx_mod.__file__).parents[1])}
    result = subprocess.run(
        [sys.executable, "-c", _BUILD_AND_DUMP.replace("KWARGS", kwargs)],
        capture_output=True, text=True, env=env, check=True,
    )
    return result.stdout


def _child_parent(schema_text: str):
    data = yaml.safe_load(schema_text)
    labels = data.get("labels", data)
    return labels["Child"].get("parent")


class DeterministicWeakNodeParentTest(unittest.TestCase):
    def test_same_schema_whatever_the_hash_seed(self) -> None:
        outputs = {seed: _schema_yaml(seed) for seed in ("0", "1", "2", "3", "4", "5")}
        self.assertEqual(len(set(outputs.values())), 1,
                         {s: _child_parent(o) for s, o in outputs.items()})

    def test_tie_broken_by_the_propagating_edge_not_by_name(self) -> None:
        """Alpha i Zeta empaten (1 propietat de pk en comú cadascun); només
        Zeta té l'aresta amb _propagate: ha de guanyar Zeta, encara que
        alfabèticament vingui després."""
        self.assertEqual(_child_parent(_schema_yaml("1")), "Zeta")


if __name__ == "__main__":
    unittest.main()
