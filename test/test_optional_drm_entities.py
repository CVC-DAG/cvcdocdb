"""`cvcdocdb.drm_entities` és un mòdul opcional.

- `import cvcdocdb` ja no l'importa: les entitats semàntiques del DRM
  (`Individu`, `IndividuPadro`, `Lloc`, `Fotografia`...) s'importen de
  `cvcdocdb.drm_entities`.
- `Atribut` (el node `Valor` que materialitza les `be_value_properties`) és
  part del nucli i viu a `cvcdocdb.base`; `drm_entities` el reexporta.
- Compatibilitat: `from cvcdocdb import IndividuPadro` (i `import *`)
  continua funcionant, amb un `DeprecationWarning`; a la propera versió
  major deixarà de funcionar.

Els tests que miren què s'ha importat corren en un subprocés per tenir un
intèrpret net (aquest procés ja té `drm_entities` importat per altres tests).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
import warnings


def _run(code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-W", "error::DeprecationWarning:cvcdocdb", "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"subprocess failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


class DrmEntitiesIsOptionalTest(unittest.TestCase):
    def test_importing_the_package_does_not_import_drm_entities(self) -> None:
        out = _run(
            """
            import sys, cvcdocdb
            print("cvcdocdb.drm_entities" in sys.modules)
            """
        )
        self.assertEqual(out, "False")

    def test_core_value_nodes_work_without_drm_entities(self) -> None:
        out = _run(
            """
            import sys
            from cvcdocdb import Atribut, Node

            class Persona(Node):
                be_value_properties = ("nom",)

            p = Persona(pk={"id": 1}, main_label="Persona", nom="Joan")
            print(type(p._dependencies["nom"]).__name__, p._dependencies["nom"].main_label,
                  "cvcdocdb.drm_entities" in sys.modules)
            """
        )
        self.assertEqual(out, "Atribut Valor False")

    def test_explicit_import_from_the_module_does_not_warn(self) -> None:
        out = _run(
            """
            from cvcdocdb.drm_entities import IndividuPadro, Atribut
            from cvcdocdb.base import Atribut as BaseAtribut
            print(IndividuPadro(pk=1, nom="Joan").main_label, Atribut is BaseAtribut)
            """
        )
        self.assertEqual(out, "IndividuPadro True")


class TopLevelAccessIsDeprecatedTest(unittest.TestCase):
    def test_top_level_access_warns_and_returns_the_same_class(self) -> None:
        import cvcdocdb
        from cvcdocdb import drm_entities

        with self.assertWarns(DeprecationWarning) as caught:
            cls = cvcdocdb.IndividuPadro
        self.assertIs(cls, drm_entities.IndividuPadro)
        self.assertIn("cvcdocdb.drm_entities", str(caught.warning))

    def test_every_drm_entity_name_is_still_reachable(self) -> None:
        import cvcdocdb
        from cvcdocdb import _DRM_ENTITY_NAMES, drm_entities

        for name in _DRM_ENTITY_NAMES:
            with self.subTest(name=name), warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                self.assertIs(getattr(cvcdocdb, name), getattr(drm_entities, name))

    def test_core_names_do_not_warn(self) -> None:
        import cvcdocdb

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            self.assertIsNotNone(cvcdocdb.Node)
            self.assertIsNotNone(cvcdocdb.Atribut)
            self.assertIsNotNone(cvcdocdb.GraphStore)

    def test_unknown_names_still_raise_attribute_error(self) -> None:
        import cvcdocdb

        with self.assertRaises(AttributeError):
            cvcdocdb.NoSuchThing  # noqa: B018

    def test_star_import_still_provides_drm_entities_with_a_warning(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", "from cvcdocdb import *; print(IndividuPadro.__name__, Node.__name__)"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "IndividuPadro Node")
