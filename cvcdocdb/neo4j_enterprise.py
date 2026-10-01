"""Optional Neo4j **Enterprise Edition** features for :class:`~cvcdocdb.neo4j_graph.Neo4jGraph`.

cvcdocdb targets **Neo4j Community Edition**: that's what its test suite and
CI run against, and ``Neo4jGraph`` defaults to ``edition="community"``. In
that mode every method in this module raises :class:`EnterpriseFeatureError`
without contacting the server.

Passing ``edition="enterprise"`` to ``Neo4jGraph`` enables them:

- :meth:`~Neo4jEnterpriseMixin.create_node_key_constraint` — ``NODE KEY``
  constraints (existence + uniqueness of a set of properties).
- :meth:`~Neo4jEnterpriseMixin.create_property_existence_constraint` —
  ``IS NOT NULL`` constraints on node or relationship properties.
- :meth:`~Neo4jEnterpriseMixin.create_property_type_constraint` —
  ``IS :: <TYPE>`` constraints (Neo4j 5.9+).
- :meth:`~Neo4jEnterpriseMixin.create_database` /
  :meth:`~Neo4jEnterpriseMixin.drop_database` — multiple user databases.

.. warning::
   Enterprise features require a valid Neo4j Enterprise license (or an
   evaluation/developer license accepted under its terms). They are **not
   fully tested**: the regular test suite only checks the generated Cypher
   and the Community-mode guards; the tests against a real Enterprise
   server run only when ``NEO4J_ENTERPRISE_URL`` is set, which CI doesn't.

None of these constraints is created automatically. In particular a
``NODE KEY`` on a label that cvcdocdb uses with several pk shapes will
reject the nodes of the other shapes — apply it only to labels with a
single pk shape.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

#: Valid values for ``Neo4jGraph(edition=...)``.
COMMUNITY_EDITION = "community"
ENTERPRISE_EDITION = "enterprise"
EDITIONS = (COMMUNITY_EDITION, ENTERPRISE_EDITION)

#: Prefix of the default constraint names created by this module.
CONSTRAINT_PREFIX = "cvcdocdb_"

ENTERPRISE_WARNING = (
    "cvcdocdb: Neo4jGraph(edition='enterprise') enables Neo4j Enterprise-only "
    "features. They require a valid Neo4j Enterprise license and are not fully "
    "tested (cvcdocdb's test suite and CI run on Neo4j Community)."
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Noms de base de dades de Neo4j 5: 3-63 caràcters, comença per lletra ASCII,
# només lletres, dígits, punts i guions.
_DATABASE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{2,62}$")
_RESERVED_DATABASES = frozenset({"system"})

# Tipus admesos per les restriccions de tipus de propietat (Neo4j 5.9+).
_PROPERTY_TYPES = frozenset({
    "BOOLEAN", "STRING", "INTEGER", "FLOAT", "DATE", "LOCAL TIME", "ZONED TIME",
    "LOCAL DATETIME", "ZONED DATETIME", "DURATION", "POINT",
})
_LIST_TYPE_RE = re.compile(r"^LIST<\s*(?P<inner>[A-Z ]+?)\s+NOT NULL\s*>$")

_ENTITIES = ("node", "relationship")


class EnterpriseFeatureError(RuntimeError):
    """An operation needs Neo4j Enterprise Edition, but the graph is in
    Community mode or the server isn't Enterprise."""


def _identifier(name: Any, kind: str) -> str:
    if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid {kind} {name!r}: must match {_IDENTIFIER_RE.pattern!r} "
            "to be used safely in a Cypher query."
        )
    return name


def _database_name(name: Any) -> str:
    if not isinstance(name, str) or not _DATABASE_NAME_RE.match(name):
        raise ValueError(
            f"Invalid database name {name!r}: 3-63 characters, starting with an "
            "ASCII letter, using only letters, digits, '.' and '-'."
        )
    if name.lower() in _RESERVED_DATABASES:
        raise ValueError(f"Database {name!r} is reserved by Neo4j.")
    return name


def _property_type(cypher_type: Any) -> str:
    if not isinstance(cypher_type, str):
        raise ValueError(f"Invalid property type {cypher_type!r}.")
    normalized = " ".join(cypher_type.upper().split())
    if normalized in _PROPERTY_TYPES:
        return normalized
    match = _LIST_TYPE_RE.match(normalized)
    if match and match.group("inner") in _PROPERTY_TYPES:
        return f"LIST<{match.group('inner')} NOT NULL>"
    raise ValueError(
        f"Unsupported property type {cypher_type!r}: use one of {sorted(_PROPERTY_TYPES)} "
        "or LIST<<type> NOT NULL>."
    )


def _pattern(entity: str, label: str) -> "tuple[str, str]":
    """``(FOR pattern, variable)`` for a node label or relationship type."""
    if entity == "node":
        return f"(n:`{label}`)", "n"
    if entity == "relationship":
        return f"()-[r:`{label}`]-()", "r"
    raise ValueError(f"Invalid entity {entity!r}: use one of {_ENTITIES}.")


def node_key_constraint_statement(
    main_label: str, properties: Iterable[str], name: Optional[str] = None
) -> "tuple[str, str]":
    """``(constraint name, CREATE CONSTRAINT statement)`` for a NODE KEY."""
    _identifier(main_label, "main_label")
    props = sorted({_identifier(prop, "property") for prop in properties})
    if not props:
        raise ValueError("A NODE KEY constraint needs at least one property.")
    name = _identifier(name, "constraint name") if name else (
        CONSTRAINT_PREFIX + "nodekey_" + "_".join((main_label, *props))
    )
    columns = ", ".join(f"n.`{prop}`" for prop in props)
    return name, (
        f"CREATE CONSTRAINT `{name}` IF NOT EXISTS "
        f"FOR (n:`{main_label}`) REQUIRE ({columns}) IS NODE KEY"
    )


def existence_constraint_statement(
    label: str, prop: str, entity: str = "node", name: Optional[str] = None
) -> "tuple[str, str]":
    """``(constraint name, CREATE CONSTRAINT statement)`` for ``IS NOT NULL``."""
    _identifier(label, "label or relationship type")
    _identifier(prop, "property")
    pattern, var = _pattern(entity, label)
    name = _identifier(name, "constraint name") if name else (
        f"{CONSTRAINT_PREFIX}exists_{label}_{prop}"
    )
    return name, (
        f"CREATE CONSTRAINT `{name}` IF NOT EXISTS "
        f"FOR {pattern} REQUIRE {var}.`{prop}` IS NOT NULL"
    )


def type_constraint_statement(
    label: str, prop: str, cypher_type: str, entity: str = "node", name: Optional[str] = None
) -> "tuple[str, str]":
    """``(constraint name, CREATE CONSTRAINT statement)`` for ``IS :: <TYPE>``."""
    _identifier(label, "label or relationship type")
    _identifier(prop, "property")
    type_expr = _property_type(cypher_type)
    pattern, var = _pattern(entity, label)
    name = _identifier(name, "constraint name") if name else (
        f"{CONSTRAINT_PREFIX}type_{label}_{prop}"
    )
    return name, (
        f"CREATE CONSTRAINT `{name}` IF NOT EXISTS "
        f"FOR {pattern} REQUIRE {var}.`{prop}` IS :: {type_expr}"
    )


def normalize_edition(edition: Any) -> str:
    """Validate ``Neo4jGraph(edition=...)``; returns it lower-cased."""
    normalized = edition.lower() if isinstance(edition, str) else edition
    if normalized not in EDITIONS:
        raise ValueError(f"Invalid Neo4j edition {edition!r}: use one of {EDITIONS}.")
    return normalized


class Neo4jEnterpriseMixin:
    """Enterprise-only operations of :class:`~cvcdocdb.neo4j_graph.Neo4jGraph`.

    Expects the host class to provide ``_driver``, ``_session``, ``_tx`` and
    ``edition``. See the module docstring for the license/testing caveats.
    """

    _driver: Any
    _session: Any
    _tx: Any
    edition: str
    _server_edition: Optional[str] = None

    def server_edition(self) -> str:
        """Edition reported by the server (``"community"`` or
        ``"enterprise"``), from ``dbms.components()``. Works in both modes."""
        if self._server_edition is None:
            record = self._session.run(
                "CALL dbms.components() YIELD edition RETURN edition"
            ).single()
            self._server_edition = str(record["edition"]).lower()
        return self._server_edition

    def drop_constraint(self, name: str) -> None:
        """Drop a constraint by name if it exists (works in both editions)."""
        self._require_no_transaction("drop_constraint")
        _identifier(name, "constraint name")
        self._session.run(f"DROP CONSTRAINT `{name}` IF EXISTS").consume()

    def create_node_key_constraint(
        self, main_label: str, properties: Iterable[str], name: Optional[str] = None
    ) -> str:
        """**Enterprise only.** Require every ``main_label`` node to have
        ``properties`` set, and their combination to be unique. Idempotent.

        Fails (server-side) if existing nodes violate it — including nodes of
        the label inserted with a different pk shape.

        Returns:
            The constraint name (``cvcdocdb_nodekey_<Label>_<props>`` by default).

        Raises:
            EnterpriseFeatureError: In Community mode or on a Community server.
        """
        self._require_enterprise("create_node_key_constraint")
        self._require_no_transaction("create_node_key_constraint")
        name, statement = node_key_constraint_statement(main_label, properties, name)
        self._run_schema_statement(statement)
        return name

    def create_property_existence_constraint(
        self, label: str, prop: str, entity: str = "node", name: Optional[str] = None
    ) -> str:
        """**Enterprise only.** Require ``prop`` on every node with ``label``
        (``entity="node"``) or every relationship of type ``label``
        (``entity="relationship"``). Idempotent.

        Returns:
            The constraint name (``cvcdocdb_exists_<label>_<prop>`` by default).

        Raises:
            EnterpriseFeatureError: In Community mode or on a Community server.
        """
        self._require_enterprise("create_property_existence_constraint")
        self._require_no_transaction("create_property_existence_constraint")
        name, statement = existence_constraint_statement(label, prop, entity, name)
        self._run_schema_statement(statement)
        return name

    def create_property_type_constraint(
        self,
        label: str,
        prop: str,
        cypher_type: str,
        entity: str = "node",
        name: Optional[str] = None,
    ) -> str:
        """**Enterprise only, Neo4j 5.9+.** Require ``prop`` (when present)
        to have Cypher type ``cypher_type`` — e.g. ``"STRING"``,
        ``"INTEGER"``, ``"LIST<STRING NOT NULL>"``. Idempotent.

        Returns:
            The constraint name (``cvcdocdb_type_<label>_<prop>`` by default).

        Raises:
            EnterpriseFeatureError: In Community mode or on a Community server.
        """
        self._require_enterprise("create_property_type_constraint")
        self._require_no_transaction("create_property_type_constraint")
        name, statement = type_constraint_statement(label, prop, cypher_type, entity, name)
        self._run_schema_statement(statement)
        return name

    def create_database(self, name: str, wait: bool = True) -> None:
        """**Enterprise only.** Create a user database if it doesn't exist.
        Runs on the ``system`` database; needs the ``CREATE DATABASE``
        privilege. With ``wait=True`` returns once the database is online.

        Raises:
            EnterpriseFeatureError: In Community mode or on a Community server.
        """
        self._require_enterprise("create_database")
        _database_name(name)
        self._run_on_system(f"CREATE DATABASE `{name}` IF NOT EXISTS" + (" WAIT" if wait else ""))

    def drop_database(self, name: str, wait: bool = True) -> None:
        """**Enterprise only.** Drop a user database (and all its data) if it
        exists. Irreversible. Runs on the ``system`` database.

        Raises:
            EnterpriseFeatureError: In Community mode or on a Community server.
        """
        self._require_enterprise("drop_database")
        _database_name(name)
        self._run_on_system(f"DROP DATABASE `{name}` IF EXISTS" + (" WAIT" if wait else ""))

    # ------------------------------------------------------------------

    def _require_enterprise(self, operation: str) -> None:
        if self.edition != ENTERPRISE_EDITION:
            raise EnterpriseFeatureError(
                f"{operation}() needs Neo4j Enterprise Edition. Neo4jGraph runs in "
                f"Community mode by default; pass edition=\"enterprise\" to enable it "
                f"(requires an Enterprise license; not fully tested)."
            )
        server = self.server_edition()
        if server != ENTERPRISE_EDITION:
            raise EnterpriseFeatureError(
                f"{operation}() needs Neo4j Enterprise Edition, but the server "
                f"reports edition {server!r}."
            )

    def _require_no_transaction(self, operation: str) -> None:
        if self._tx is not None:
            raise RuntimeError(
                f"{operation}() can't run inside batch() or an open transaction: "
                "Neo4j doesn't allow schema changes in a transaction that writes data"
            )

    def _run_schema_statement(self, statement: str) -> None:
        self._session.run(statement).consume()

    def _run_on_system(self, statement: str) -> None:
        session = self._driver.session(database="system")
        try:
            session.run(statement).consume()
        finally:
            session.close()
