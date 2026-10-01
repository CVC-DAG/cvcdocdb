"""Neo4jGraph no emet el PreviewWarning del driver de Neo4j.

Amb el driver 5.x (el que s'instal·la a Python 3.9), l'API de filtres de
notificacions per "classifications" (`NotificationDisabledClassification`,
`notifications_disabled_classifications`) és una funcionalitat en preview i
el driver avisa amb un `PreviewWarning` en importar-la i en obrir cada
sessió. La 5.x té l'equivalent estable per "categories"
(`NotificationDisabledCategory`, `notifications_disabled_categories`); la
6.x, en canvi, declara obsoletes les categories i les classifications ja
són estables. cvcdocdb tria l'API estable de cada versió: cap avís, i el
filtre de DEPRECATION es manté.

Es comprova amb un paquet `neo4j` fals (en un subprocés) que reprodueix el
comportament de cada versió del driver.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_FAKE_DRIVER = '''
import enum, warnings

__version__ = "{version}"
_PREVIEW = {preview!r}       # noms en preview en aquesta versió del driver
_DEPRECATED = {deprecated!r} # noms obsolets en aquesta versió del driver
SESSION_KWARGS = []


class PreviewWarning(Warning):
    pass


def _check(name):
    if name in _PREVIEW:
        warnings.warn(name + " is a preview feature", PreviewWarning, stacklevel=3)
    if name in _DEPRECATED:
        warnings.warn(name + " is deprecated", DeprecationWarning, stacklevel=3)


class _Category(str, enum.Enum):
    DEPRECATION = "DEPRECATION"


class _Classification(str, enum.Enum):
    DEPRECATION = "DEPRECATION"


_LAZY = {{"NotificationDisabledCategory": _Category, "NotificationDisabledClassification": _Classification}}


def __getattr__(name):  # com el driver real: avisa en accedir-hi
    if name in _LAZY:
        _check(name)
        return _LAZY[name]
    raise AttributeError(name)


class _Info:
    protocol_version = (5, 4)


class _Session:
    def close(self):
        pass


class _Driver:
    def get_server_info(self):
        return _Info()

    def session(self, **kwargs):
        for key in kwargs:
            _check(key)
        SESSION_KWARGS.append(kwargs)
        return _Session()

    def close(self):
        pass


class GraphDatabase:
    @staticmethod
    def driver(*args, **kwargs):
        return _Driver()
'''

_PROBE = textwrap.dedent(
    """
    import warnings
    warnings.simplefilter("error")
    import neo4j
    from cvcdocdb.neo4j_graph import Neo4jGraph
    Neo4jGraph("bolt://fake:7687", "u", "p")
    (kwargs,) = neo4j.SESSION_KWARGS
    print(sorted(kwargs), [str(v.value) for v in next(iter(kwargs.values()))])
    """
)


def _run_with_fake_driver(version: str, preview: tuple, deprecated: tuple) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        pkg = os.path.join(tmp, "neo4j")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "__init__.py"), "w") as fh:
            fh.write(_FAKE_DRIVER.format(version=version, preview=preview, deprecated=deprecated))
        with open(os.path.join(pkg, "exceptions.py"), "w") as fh:
            fh.write("class ConstraintError(Exception): pass\n"
                     "class Forbidden(Exception): pass\n"
                     "class TransactionError(Exception): pass\n")
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([tmp, _REPO])}
        result = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise AssertionError(f"probe failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


class NotificationFilterApiTest(unittest.TestCase):
    def test_driver_5_uses_the_stable_categories_api_without_warnings(self) -> None:
        out = _run_with_fake_driver(
            "5.28.6",
            preview=("NotificationDisabledClassification", "notifications_disabled_classifications"),
            deprecated=(),
        )
        self.assertEqual(out, "['notifications_disabled_categories'] ['DEPRECATION']")

    def test_driver_6_uses_the_classifications_api_without_warnings(self) -> None:
        out = _run_with_fake_driver(
            "6.2.0",
            preview=(),
            deprecated=("NotificationDisabledCategory", "notifications_disabled_categories"),
        )
        self.assertEqual(out, "['notifications_disabled_classifications'] ['DEPRECATION']")
