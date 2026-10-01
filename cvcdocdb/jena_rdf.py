"""Mapping between the state of a cvcdocdb graph and RDF 1.2 triples.

Used by :class:`~cvcdocdb.jena_graph.JenaGraph` to store, in a SPARQL store
such as Apache Jena Fuseki, exactly the state ``NetworkXGraph`` keeps in
memory. The RDF is meant to be queried directly with SPARQL. With the
default namespace ``urn:cvcdocdb:``:

- a node is ``<urn:cvcdocdb:node/ID>``; it is ``a <urn:cvcdocdb:label/L>``
  for each of its labels, and each property ``name`` is a triple
  ``<urn:cvcdocdb:prop/name> value``;
- a relation is the triple ``<node/U> <urn:cvcdocdb:rel/TYPE> <node/V>``.
  Its properties hang from a reifier ``<urn:cvcdocdb:edge/U/V/TYPE>`` with
  ``rdf:reifies <<( <node/U> <rel/TYPE> <node/V> )>>`` (RDF 1.2);
- bookkeeping lives under ``<urn:cvcdocdb:meta/...>``: each node's main
  label and pk, the label order, and the id counter.

Values: ``str`` → plain literal, ``bool`` → ``xsd:boolean``, ``int`` →
``xsd:integer``, ``float`` → ``xsd:double``, ``date``/``datetime`` →
``xsd:date``/``xsd:dateTime``, and anything else (lists, dicts, ``None``) →
an ``rdf:JSON`` literal. Names (labels, properties, relation types) are
percent-encoded into the IRIs.

Terms are handled in two forms: canonical N-Triples-like strings (what is
sent to the server, and what triples are compared by) and parsed tuples
(``("uri", iri)``, ``("literal", lexical, datatype, lang)``,
``("triple", s, p, o)``).
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote, unquote

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XSD = "http://www.w3.org/2001/XMLSchema#"
RDF_TYPE = f"<{RDF}type>"
RDF_REIFIES = f"<{RDF}reifies>"
RDF_JSON = f"{RDF}JSON"

DEFAULT_NAMESPACE = "urn:cvcdocdb:"

#: Node attributes that are representation, not properties (see NetworkXGraph).
_REPRESENTATION_KEYS = ("pk", "main_label", "labels")

Term = Tuple[Any, ...]
Triple = Tuple[str, str, str]

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _escape(text: str) -> str:
    return "".join(_ESCAPES.get(ch, ch) for ch in text)


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "r": "\r", "t": "\t"}.get(m.group(1), m.group(1)), text)


def _name(name: Any) -> str:
    """Percent-encode a label/property/relation name for use inside an IRI."""
    return quote(str(name), safe="")


def term_to_string(term: Term) -> str:
    """Canonical string form of a parsed term."""
    kind = term[0]
    if kind == "uri":
        return f"<{term[1]}>"
    if kind == "literal":
        _, lexical, datatype, lang = term
        text = f'"{_escape(lexical)}"'
        if lang:
            return f"{text}@{lang}"
        if datatype and datatype != f"{XSD}string":
            return f"{text}^^<{datatype}>"
        return text
    if kind == "triple":
        return "<<( " + " ".join(term_to_string(t) for t in term[1:]) + " )>>"
    raise ValueError(f"Unsupported RDF term {term!r}")


_TERM_RE = re.compile(
    r'\s*(?:<<\(|<([^>]*)>|"((?:[^"\\]|\\.)*)"(?:@([A-Za-z0-9-]+)|\^\^<([^>]*)>)?|_:(\S+))'
)


def parse_term(text: str) -> Term:
    """Parse a canonical term string (see :func:`term_to_string`)."""
    term, rest = _parse_term(text)
    if rest.strip():
        raise ValueError(f"Trailing text after RDF term: {text!r}")
    return term


def _parse_term(text: str) -> Tuple[Term, str]:
    match = _TERM_RE.match(text)
    if not match:
        raise ValueError(f"Not an RDF term: {text!r}")
    rest = text[match.end():]
    if match.group(0).strip() == "<<(":
        s, rest = _parse_term(rest)
        p, rest = _parse_term(rest)
        o, rest = _parse_term(rest)
        rest = rest.lstrip()
        if not rest.startswith(")>>"):
            raise ValueError(f"Unterminated triple term: {text!r}")
        return ("triple", s, p, o), rest[3:]
    if match.group(1) is not None:
        return ("uri", match.group(1)), rest
    if match.group(5) is not None:
        return ("bnode", match.group(5)), rest
    datatype = match.group(4)
    return ("literal", _unescape(match.group(2)), datatype, match.group(3)), rest


def term_from_binding(binding: Dict[str, Any]) -> Term:
    """Parsed term from a SPARQL 1.2 JSON results binding."""
    kind = binding["type"]
    if kind == "uri":
        return ("uri", binding["value"])
    if kind in ("literal", "typed-literal"):
        datatype = binding.get("datatype")
        if datatype == f"{XSD}string":
            datatype = None
        return ("literal", binding["value"], datatype, binding.get("xml:lang"))
    if kind == "bnode":
        return ("bnode", binding["value"])
    if kind == "triple":
        value = binding["value"]
        return ("triple", term_from_binding(value["subject"]),
                term_from_binding(value["predicate"]), term_from_binding(value["object"]))
    raise ValueError(f"Unsupported SPARQL binding {binding!r}")


def _float_lexical(value: float) -> str:
    if value != value:
        return "NaN"
    if value in (float("inf"), float("-inf")):
        return "INF" if value > 0 else "-INF"
    return repr(value)


class RdfMapping:
    """IRIs and value encoding for one namespace (see the module docstring)."""

    def __init__(self, namespace: str = DEFAULT_NAMESPACE) -> None:
        if not namespace or any(ch in namespace for ch in '<>" {}|\\^`'):
            raise ValueError(f"Invalid namespace {namespace!r}")
        self.namespace = namespace
        self.meta_state = f"<{namespace}meta/state>"
        self.meta_sync = f"<{namespace}meta/sync>"
        self._p_main_label = f"<{namespace}meta/mainLabel>"
        self._p_labels = f"<{namespace}meta/labels>"
        self._p_pk = f"<{namespace}meta/pk>"
        self._p_counter = f"<{namespace}meta/nodeCounter>"
        self._p_version = f"<{namespace}meta/stateVersion>"
        self.p_sync_version = f"<{namespace}meta/syncVersion>"
        self.p_commit = f"<{namespace}meta/commit>"

    # -- IRIs --------------------------------------------------------------

    def node_iri(self, node_id: int) -> str:
        return f"<{self.namespace}node/{int(node_id)}>"

    def label_iri(self, label: str) -> str:
        return f"<{self.namespace}label/{_name(label)}>"

    def prop_iri(self, name: str) -> str:
        return f"<{self.namespace}prop/{_name(name)}>"

    def rel_iri(self, rel_type: str) -> str:
        return f"<{self.namespace}rel/{_name(rel_type)}>"

    def edge_iri(self, src: int, dst: int, rel_type: str) -> str:
        return f"<{self.namespace}edge/{int(src)}/{int(dst)}/{_name(rel_type)}>"

    def _local(self, iri: str, kind: str) -> Optional[str]:
        prefix = f"{self.namespace}{kind}/"
        return iri[len(prefix):] if iri.startswith(prefix) else None

    # -- values ------------------------------------------------------------

    def encode_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return f'"{"true" if value else "false"}"^^<{XSD}boolean>'
        if isinstance(value, int):
            return f'"{value}"^^<{XSD}integer>'
        if isinstance(value, float):
            return f'"{_float_lexical(value)}"^^<{XSD}double>'
        if isinstance(value, str):
            return f'"{_escape(value)}"'
        if isinstance(value, datetime.datetime):
            return f'"{value.isoformat()}"^^<{XSD}dateTime>'
        if isinstance(value, datetime.date):
            return f'"{value.isoformat()}"^^<{XSD}date>'
        text = json.dumps(value, ensure_ascii=False, sort_keys=False, default=str)
        return f'"{_escape(text)}"^^<{RDF_JSON}>'

    def decode_term(self, term: Term) -> Any:
        if term[0] == "uri":
            return term[1]
        if term[0] != "literal":
            raise ValueError(f"Cannot decode {term!r} as a value")
        _, lexical, datatype, _ = term
        if datatype is None:
            return lexical
        if datatype == f"{XSD}boolean":
            return lexical in ("true", "1")
        if datatype in (f"{XSD}integer", f"{XSD}int", f"{XSD}long"):
            return int(lexical)
        if datatype in (f"{XSD}double", f"{XSD}float", f"{XSD}decimal"):
            return float({"INF": "inf", "-INF": "-inf"}.get(lexical, lexical))
        if datatype == f"{XSD}dateTime":
            return datetime.datetime.fromisoformat(lexical)
        if datatype == f"{XSD}date":
            return datetime.date.fromisoformat(lexical)
        if datatype == RDF_JSON:
            return json.loads(lexical)
        return lexical

    # -- state <-> triples -------------------------------------------------

    def state_to_triples(
        self,
        node_attrs: Dict[int, Dict[str, Any]],
        edge_attrs: Dict[Tuple[int, int, str], Dict[str, Any]],
        meta: Dict[str, Any],
    ) -> Set[Triple]:
        """Every triple that represents *node_attrs*/*edge_attrs*/*meta*."""
        triples: Set[Triple] = set()
        for node_id, attrs in node_attrs.items():
            node = self.node_iri(node_id)
            labels = list(attrs.get("labels") or [])
            for label in labels:
                triples.add((node, RDF_TYPE, self.label_iri(label)))
            triples.add((node, self._p_main_label, self.encode_value(attrs.get("main_label", ""))))
            triples.add((node, self._p_labels, self.encode_value(labels)))
            triples.add((node, self._p_pk, self.encode_value(attrs.get("pk"))))
            for name, value in attrs.items():
                if name not in _REPRESENTATION_KEYS:
                    triples.add((node, self.prop_iri(name), self.encode_value(value)))
        for (src, dst, rel_type), props in edge_attrs.items():
            base = (self.node_iri(src), self.rel_iri(rel_type), self.node_iri(dst))
            triples.add(base)
            reifier = self.edge_iri(src, dst, rel_type)
            triples.add((reifier, RDF_REIFIES, "<<( " + " ".join(base) + " )>>"))
            for name, value in (props or {}).items():
                triples.add((reifier, self.prop_iri(name), self.encode_value(value)))
        triples.add((self.meta_state, self._p_counter, self.encode_value(int(meta.get("node_counter", 0)))))
        triples.add((self.meta_state, self._p_version, self.encode_value(meta.get("version", 0.0))))
        return triples

    def state_from_triples(
        self, triples: Iterable[Tuple[Term, Term, Term]]
    ) -> Tuple[Dict[int, Dict[str, Any]], Dict[Tuple[int, int, str], Dict[str, Any]], Dict[str, Any]]:
        """Inverse of :meth:`state_to_triples`. Triples outside the namespace
        (or not written by cvcdocdb) are ignored."""
        nodes: Dict[int, Dict[str, Any]] = {}
        node_meta: Dict[int, Dict[str, Any]] = {}
        edges: Dict[Tuple[int, int, str], Dict[str, Any]] = {}
        reifier_props: Dict[Tuple[int, int, str], Dict[str, Any]] = {}
        meta: Dict[str, Any] = {"node_counter": 0, "version": 0.0}

        for s, p, o in triples:
            if s[0] != "uri" or p[0] != "uri":
                continue
            subject, predicate = f"<{s[1]}>", f"<{p[1]}>"
            if subject == self.meta_state:
                if predicate == self._p_counter:
                    meta["node_counter"] = self.decode_term(o)
                elif predicate == self._p_version:
                    meta["version"] = self.decode_term(o)
                continue
            node_local = self._local(s[1], "node")
            if node_local is not None and node_local.isdigit():
                node_id = int(node_local)
                if predicate == RDF_TYPE:
                    continue  # labels come from meta/labels (keeps their order)
                if predicate in (self._p_main_label, self._p_labels, self._p_pk):
                    key = {self._p_main_label: "main_label", self._p_labels: "labels", self._p_pk: "pk"}[predicate]
                    node_meta.setdefault(node_id, {})[key] = self.decode_term(o)
                    continue
                prop = self._local(p[1], "prop")
                if prop is not None:
                    nodes.setdefault(node_id, {})[unquote(prop)] = self.decode_term(o)
                    continue
                rel = self._local(p[1], "rel")
                dst = self._local(o[1], "node") if o[0] == "uri" else None
                if rel is not None and dst is not None and dst.isdigit():
                    edges.setdefault((node_id, int(dst), unquote(rel)), {})
                continue
            edge_local = self._local(s[1], "edge")
            if edge_local is not None:
                parts = edge_local.split("/", 2)
                if len(parts) != 3 or not (parts[0].isdigit() and parts[1].isdigit()):
                    continue
                key = (int(parts[0]), int(parts[1]), unquote(parts[2]))
                props = reifier_props.setdefault(key, {})
                prop = self._local(p[1], "prop")
                if prop is not None:
                    props[unquote(prop)] = self.decode_term(o)

        node_attrs: Dict[int, Dict[str, Any]] = {}
        for node_id in sorted(set(nodes) | set(node_meta)):
            info = node_meta.get(node_id, {})
            node_attrs[node_id] = {
                "pk": info.get("pk"),
                "main_label": info.get("main_label", ""),
                "labels": list(info.get("labels") or []),
                **nodes.get(node_id, {}),
            }
        edge_attrs = {key: reifier_props.get(key, {}) for key in edges}
        return node_attrs, edge_attrs, meta

    @staticmethod
    def subjects(triples: Iterable[Triple]) -> Dict[str, FrozenSet[Triple]]:
        """Group triples by subject."""
        grouped: Dict[str, List[Triple]] = {}
        for triple in triples:
            grouped.setdefault(triple[0], []).append(triple)
        return {subject: frozenset(items) for subject, items in grouped.items()}
