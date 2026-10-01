"""Correspondència entre l'estat d'un graf cvcdocdb i RDF (sense servidor).

`JenaGraph` desa a Fuseki el mateix estat que `NetworkXGraph` té en memòria
(atributs de nodes i de relacions, comptador d'ids). Aquests tests comproven
que el pas estat → triples RDF → estat és exacte per a tots els tipus de
valor, i que l'RDF és llegible des de SPARQL:

- node: ``<ns:node/ID> a <ns:label/Etiqueta>``, ``<ns:prop/nom> "valor"``;
- relació: ``<ns:node/U> <ns:rel/TIPUS> <ns:node/V>``, amb un reificador
  ``<ns:edge/U/V/TIPUS> rdf:reifies <<( ... )>>`` que porta les propietats
  de la relació (RDF 1.2).
"""

from __future__ import annotations

import datetime
import unittest

from cvcdocdb.jena_rdf import RdfMapping, parse_term


class LiteralRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mapping = RdfMapping()

    def roundtrip(self, value):
        return self.mapping.decode_term(parse_term(self.mapping.encode_value(value)))

    def test_values_keep_their_type_and_content(self) -> None:
        values = [
            "text", "", 'quotes " and \\ backslash', "line\nbreak\ttab\r", "Padró àèïòú 漢字 🙂",
            0, 1, -7, 10**20, 1.5, -0.25, 1e300, True, False, None,
            [1, "a", None], {"a": 1, "b": [True, None]}, [], {},
            datetime.date(1905, 3, 1), datetime.datetime(2026, 10, 1, 12, 30, 15),
        ]
        for value in values:
            with self.subTest(value=value):
                decoded = self.roundtrip(value)
                self.assertEqual(decoded, value)
                self.assertIs(type(decoded), type(value))

    def test_booleans_are_not_integers(self) -> None:
        self.assertIn("boolean", self.mapping.encode_value(True))
        self.assertIn("integer", self.mapping.encode_value(1))


class StateRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mapping = RdfMapping("urn:test:")
        self.node_attrs = {
            1: {"pk": {"doc": "D1"}, "main_label": "Document", "labels": ["Document", "Item"],
                "doc": "D1", "title": "Padró 1905", "year": 1905, "_weak_init_done": True},
            2: {"pk": {"doc": "D1", "sec": 1}, "main_label": "Section", "labels": ["Section"],
                "doc": "D1", "sec": 1, "is_weak": True, "parent_relation": "HAS_SECTION",
                "weird name / with spaces": "ok"},
            3: {"pk": None, "main_label": "Note", "labels": ["Note"], "text": None},
        }
        self.edge_attrs = {
            (1, 2, "HAS_SECTION"): {"_propagate": True, "order": 1},
            (3, 1, "ABOUT"): {},
        }
        self.meta = {"node_counter": 3, "version": 0.0}

    def roundtrip(self):
        triples = self.mapping.state_to_triples(self.node_attrs, self.edge_attrs, self.meta)
        parsed = [tuple(parse_term(term) for term in triple) for triple in triples]
        return self.mapping.state_from_triples(parsed)

    def test_state_survives_the_round_trip(self) -> None:
        node_attrs, edge_attrs, meta = self.roundtrip()
        self.assertEqual(node_attrs, self.node_attrs)
        self.assertEqual(edge_attrs, self.edge_attrs)
        self.assertEqual(meta, self.meta)

    def test_label_order_and_main_label_are_kept(self) -> None:
        node_attrs, _, _ = self.roundtrip()
        self.assertEqual(node_attrs[1]["labels"], ["Document", "Item"])
        self.assertEqual(node_attrs[1]["main_label"], "Document")

    def test_rdf_is_readable_from_sparql(self) -> None:
        triples = set(self.mapping.state_to_triples(self.node_attrs, self.edge_attrs, self.meta))
        rdf_type = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
        self.assertIn(("<urn:test:node/1>", rdf_type, "<urn:test:label/Document>"), triples)
        self.assertIn(("<urn:test:node/1>", "<urn:test:prop/title>", '"Padró 1905"'), triples)
        self.assertIn(("<urn:test:node/1>", "<urn:test:rel/HAS_SECTION>", "<urn:test:node/2>"), triples)
        self.assertIn(
            ("<urn:test:edge/1/2/HAS_SECTION>", "<http://www.w3.org/1999/02/22-rdf-syntax-ns#reifies>",
             "<<( <urn:test:node/1> <urn:test:rel/HAS_SECTION> <urn:test:node/2> )>>"),
            triples,
        )

    def test_property_names_are_iri_safe(self) -> None:
        triples = self.mapping.state_to_triples(self.node_attrs, self.edge_attrs, self.meta)
        predicates = {p for _, p, _ in triples}
        self.assertIn("<urn:test:prop/weird%20name%20%2F%20with%20spaces>", predicates)

    def test_unsafe_labels_and_relation_types_are_encoded(self) -> None:
        node_attrs = {1: {"pk": None, "main_label": "A>B", "labels": ["A>B"]}}
        triples = self.mapping.state_to_triples(node_attrs, {}, {"node_counter": 1, "version": 0.0})
        for triple in triples:
            for term in triple:
                if term.startswith("<") and not term.startswith("<<("):
                    self.assertNotIn(">", term[1:-1])

    def test_triples_are_grouped_by_subject(self) -> None:
        by_subject = self.mapping.subjects(self.mapping.state_to_triples(self.node_attrs, self.edge_attrs, self.meta))
        self.assertIn("<urn:test:node/1>", by_subject)
        self.assertIn("<urn:test:edge/1/2/HAS_SECTION>", by_subject)
        self.assertTrue(all(t[0] == "<urn:test:node/2>" for t in by_subject["<urn:test:node/2>"]))

    def test_triples_outside_the_namespace_are_ignored(self) -> None:
        triples = list(self.mapping.state_to_triples(self.node_attrs, self.edge_attrs, self.meta))
        triples.append(("<http://other/x>", "<http://other/p>", '"y"'))
        parsed = [tuple(parse_term(term) for term in triple) for triple in triples]
        node_attrs, _, _ = self.mapping.state_from_triples(parsed)
        self.assertEqual(sorted(node_attrs), [1, 2, 3])


class ParseTermTest(unittest.TestCase):
    def test_parses_iris_literals_and_triple_terms(self) -> None:
        self.assertEqual(parse_term("<urn:a>"), ("uri", "urn:a"))
        self.assertEqual(parse_term('"x"'), ("literal", "x", None, None))
        self.assertEqual(
            parse_term('"1"^^<http://www.w3.org/2001/XMLSchema#integer>'),
            ("literal", "1", "http://www.w3.org/2001/XMLSchema#integer", None),
        )
        self.assertEqual(parse_term('"hola"@ca'), ("literal", "hola", None, "ca"))
        self.assertEqual(
            parse_term("<<( <urn:s> <urn:p> <urn:o> )>>"),
            ("triple", ("uri", "urn:s"), ("uri", "urn:p"), ("uri", "urn:o")),
        )

    def test_sparql_json_bindings_are_parsed_to_the_same_terms(self) -> None:
        from cvcdocdb.jena_rdf import term_from_binding

        self.assertEqual(term_from_binding({"type": "uri", "value": "urn:a"}), ("uri", "urn:a"))
        self.assertEqual(
            term_from_binding({"type": "literal", "value": "1", "datatype": "http://www.w3.org/2001/XMLSchema#integer"}),
            ("literal", "1", "http://www.w3.org/2001/XMLSchema#integer", None),
        )
        self.assertEqual(
            term_from_binding({"type": "triple", "value": {
                "subject": {"type": "uri", "value": "urn:s"},
                "predicate": {"type": "uri", "value": "urn:p"},
                "object": {"type": "uri", "value": "urn:o"}}}),
            ("triple", ("uri", "urn:s"), ("uri", "urn:p"), ("uri", "urn:o")),
        )
