"""Escenaris de la política de propagació de canvis (inserció, actualització,
esborrat i relacions), compartits pels tests que comparen backends.

Cada escenari rep un graf buit, hi fa operacions i retorna el resultat de
les que interessen (``("ok", "")`` o ``(nom de l'excepció, inici del
missatge)``). :func:`run_scenario` hi afegeix una foto normalitzada del graf
resultant, independent dels identificadors interns.
"""

from __future__ import annotations

import warnings
from typing import Any, Callable, Dict, Iterable, List, Tuple

from cvcdocdb.base import Node, Relation, WeakNode
from cvcdocdb.drm_entities import IndividuPadro

Outcome = Tuple[str, str]  # (nom de l'excepció o "ok", inici del missatge)


def attempt(action: Callable[[], Any]) -> Outcome:
    try:
        action()
    except Exception as exc:  # noqa: BLE001 - el tipus forma part del resultat
        return type(exc).__name__, str(exc).split(":")[0]
    return "ok", ""


#: Atributs que NetworkXGraph desa al node però Neo4j/Memgraph no (els
#: representen amb etiquetes o no els guarden): no formen part de la política.
REPRESENTATION_KEYS = frozenset({"pk", "labels", "main_label"})


def snapshot(graph: Any, ignore: Iterable[str] = ()) -> Dict[str, Any]:
    """Graf normalitzat, independent dels identificadors interns."""
    ignored = frozenset(ignore)
    rows = graph.query("MATCH (n) RETURN id(n) AS id, labels(n) AS labels, properties(n) AS props")
    keys: Dict[int, Tuple[Any, ...]] = {}
    nodes = []
    for row in rows:
        props = {k: v for k, v in row["props"].items() if k not in ignored}
        key = (tuple(sorted(row["labels"])), tuple(sorted((k, repr(v)) for k, v in props.items())))
        keys[row["id"]] = key
        nodes.append(key)
    edges = [
        (keys[row["src"]], row["type"], keys[row["dst"]], tuple(sorted((k, repr(v)) for k, v in row["props"].items())))
        for row in graph.query(
            "MATCH (a)-[r]->(b) RETURN id(a) AS src, type(r) AS type, id(b) AS dst, properties(r) AS props"
        )
    ]
    return {"nodes": sorted(nodes), "edges": sorted(edges)}


def document_tree() -> Tuple[Node, WeakNode, WeakNode]:
    doc = Node(pk={"doc": "D1"}, main_label="Document", title="Padró")
    sec = WeakNode(parent=doc, pk={"sec": 1}, main_label="Section", parent_relation="HAS_SECTION")
    page = WeakNode(parent=sec, pk={"page": 7}, main_label="Page", parent_relation="HAS_PAGE", text="...")
    return doc, sec, page


def scenario_weak_chain_inserts_its_parents(graph: Any) -> List[Outcome]:
    _, _, page = document_tree()
    return [attempt(lambda: graph.insertNode(page))]


def scenario_weak_node_without_parent_is_refused(graph: Any) -> List[Outcome]:
    _, sec, _ = document_tree()
    return [attempt(lambda: graph.insertNode(sec, insert_parent=False))]


def scenario_child_keys_must_match_parent(graph: Any) -> List[Outcome]:
    doc, _, _ = document_tree()
    graph.insertNode(doc)
    other = Node(pk={"doc": "D2"}, main_label="Document")
    sec = WeakNode(parent=other, pk={"sec": 1}, main_label="Section", parent_relation="HAS_SECTION")
    sec._parent = doc  # el fill diu que el pare és D1, però la seva clau porta D2
    return [attempt(lambda: graph.insertNode(sec, insert_parent=False))]


def scenario_duplicate_key_is_refused(graph: Any) -> List[Outcome]:
    graph.insertNode(Node(pk={"doc": "D1"}, main_label="Document"))
    return [attempt(lambda: graph.insertNode(Node(pk={"doc": "D1"}, main_label="Document", title="x")))]


def scenario_update_merges_attributes(graph: Any) -> List[Outcome]:
    graph.insertNode(Node(pk={"doc": "D1"}, main_label="Document", title="A", lang="ca"))
    return [
        attempt(lambda: graph.insertNode(Node(pk={"doc": "D1"}, main_label="Document", title="B", year=1900), update=True)),
        attempt(lambda: graph.insertNode(Node(pk={"doc": "D2"}, main_label="Document", title="C"), update=True)),
    ]


def scenario_update_of_weak_node_keeps_one_parent_edge(graph: Any) -> List[Outcome]:
    _, _, page = document_tree()
    graph.insertNode(page)
    _, _, page2 = document_tree()
    page2["text"] = "nou text"
    return [attempt(lambda: graph.insertNode(page2, update=True))]


def scenario_replace_deletes_weak_children(graph: Any) -> List[Outcome]:
    _, _, page = document_tree()
    graph.insertNode(page)
    return [attempt(lambda: graph.insertNode(Node(pk={"doc": "D1"}, main_label="Document", title="nou"), replace=True))]


def scenario_dependencies_become_valor_nodes(graph: Any) -> List[Outcome]:
    person = IndividuPadro(pk=123, nom="Joan", cognom1="Miró", cognom2="Ferrer")
    return [attempt(lambda: graph.insertNode(person))]


def scenario_restrict_refuses_node_with_weak_children(graph: Any) -> List[Outcome]:
    doc, _, page = document_tree()
    graph.insertNode(page)
    return [attempt(lambda: graph.deleteNode(doc))]


def scenario_restrict_refuses_node_with_edges(graph: Any) -> List[Outcome]:
    a, b = Node(pk={"id": 1}, main_label="T"), Node(pk={"id": 2}, main_label="T")
    graph.insertNode(a)
    graph.insertNode(b)
    graph.insertRelation(Relation(a, b, "LINKS"))
    return [attempt(lambda: graph.deleteNode(b)), attempt(lambda: graph.deleteNode(a))]


def scenario_restrict_deletes_isolated_node(graph: Any) -> List[Outcome]:
    a = Node(pk={"id": 1}, main_label="T")
    graph.insertNode(a)
    graph.insertNode(Node(pk={"id": 2}, main_label="T"))
    return [attempt(lambda: graph.deleteNode(a))]


def scenario_propagation_deletes_weak_descendants(graph: Any) -> List[Outcome]:
    doc, _, page = document_tree()
    graph.insertNode(page)
    other = Node(pk={"doc": "D9"}, main_label="Document")
    graph.insertNode(other)
    graph.insertRelation(Relation(doc, other, "CITES"))
    return [attempt(lambda: graph.deleteNode(doc, propagation=True, detach=True))]


def scenario_propagation_without_detach(graph: Any) -> List[Outcome]:
    doc, _, page = document_tree()
    graph.insertNode(page)
    return [attempt(lambda: graph.deleteNode(doc, propagation=True))]


def scenario_detach_cascade_keeps_neighbours(graph: Any) -> List[Outcome]:
    a, b, c = (Node(pk={"id": i}, main_label="T") for i in (1, 2, 3))
    for n in (a, b, c):
        graph.insertNode(n)
    graph.insertRelation(Relation(a, b, "LINKS"))
    graph.insertRelation(Relation(c, a, "LINKS"))
    return [attempt(lambda: graph.deleteNode(a, detach=True))]


def scenario_set_null_keeps_neighbours(graph: Any) -> List[Outcome]:
    a, b = Node(pk={"id": 1}, main_label="T"), Node(pk={"id": 2}, main_label="T")
    graph.insertNode(a)
    graph.insertNode(b)
    graph.insertRelation(Relation(a, b, "LINKS"))
    return [attempt(lambda: graph.deleteNode(a, on_delete="set_null"))]


def scenario_relation_fk_violation(graph: Any) -> List[Outcome]:
    a = Node(pk={"id": 1}, main_label="T")
    graph.insertNode(a)
    ghost = Node(pk={"id": 99}, main_label="T")
    return [
        attempt(lambda: graph.insertRelation(Relation(a, ghost, "LINKS"))),
        attempt(lambda: graph.insertRelation(Relation(ghost, a, "LINKS"))),
    ]


def scenario_relation_update_and_replace(graph: Any) -> List[Outcome]:
    a, b = Node(pk={"id": 1}, main_label="T"), Node(pk={"id": 2}, main_label="T")
    graph.insertNode(a)
    graph.insertNode(b)
    return [
        attempt(lambda: graph.insertRelation(Relation(a, b, "LINKS", weight=1), update=True)),
        attempt(lambda: graph.insertRelation(Relation(a, b, "LINKS", weight=2, kind="x"), update=True)),
        attempt(lambda: graph.insertRelation(Relation(a, b, "LINKS", weight=3), replace=True)),
    ]


def scenario_failed_insert_inside_batch_rolls_back(graph: Any) -> List[Outcome]:
    _, sec, _ = document_tree()

    def run() -> None:
        with graph.batch():
            graph.insertNode(Node(pk={"id": 1}, main_label="T"))
            graph.insertNode(sec, insert_parent=False)

    return [attempt(run)]


def scenario_create_group_then_propagated_delete(graph: Any) -> List[Outcome]:
    doc = Node(pk={"doc": "G1"}, main_label="Document")
    sec = WeakNode(parent=doc, pk={"sec": 1}, main_label="Section", parent_relation="HAS_SECTION")
    sec2 = WeakNode(parent=doc, pk={"sec": 2}, main_label="Section", parent_relation="HAS_SECTION")
    outcomes = [attempt(lambda: graph.create_group(doc, weak_nodes=[sec, sec2]))]
    outcomes.append(attempt(lambda: graph.deleteNode(doc)))
    return outcomes


def scenario_init_propagation_marks_weak_edges(graph: Any) -> List[Outcome]:
    _, _, page = document_tree()
    graph.insertNode(page)
    return [attempt(lambda: graph.init_propagation())]


def scenario_init_propagation_marks_edges_into_weak_nodes(graph: Any) -> List[Outcome]:
    """init_propagation() marca amb _propagate qualsevol relació sense el
    flag que arribi a un node is_weak (pas 3 de Neo4jGraph), i per tant
    l'esborrat amb propagació des d'un node que hi apunta s'emporta el fill."""
    _, sec, page = document_tree()
    graph.insertNode(page)
    reader = Node(pk={"id": 1}, main_label="Reader")
    graph.insertNode(reader)
    graph.insertRelation(Relation(reader, sec, "CITES"))
    return [
        attempt(lambda: graph.init_propagation()),
        attempt(lambda: graph.deleteNode(reader, propagation=True, detach=True)),
    ]


SCENARIOS = [
    scenario_weak_chain_inserts_its_parents,
    scenario_weak_node_without_parent_is_refused,
    scenario_child_keys_must_match_parent,
    scenario_duplicate_key_is_refused,
    scenario_update_merges_attributes,
    scenario_update_of_weak_node_keeps_one_parent_edge,
    scenario_replace_deletes_weak_children,
    scenario_dependencies_become_valor_nodes,
    scenario_restrict_refuses_node_with_weak_children,
    scenario_restrict_refuses_node_with_edges,
    scenario_restrict_deletes_isolated_node,
    scenario_propagation_deletes_weak_descendants,
    scenario_propagation_without_detach,
    scenario_detach_cascade_keeps_neighbours,
    scenario_set_null_keeps_neighbours,
    scenario_relation_fk_violation,
    scenario_relation_update_and_replace,
    scenario_failed_insert_inside_batch_rolls_back,
    scenario_create_group_then_propagated_delete,
    scenario_init_propagation_marks_weak_edges,
    scenario_init_propagation_marks_edges_into_weak_nodes,
]


def run_scenario(
    make_graph: Callable[[], Any], scenario: Callable[[Any], List[Outcome]], ignore: Iterable[str] = ()
) -> Dict[str, Any]:
    graph = make_graph()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            outcomes = scenario(graph)
        return {"outcomes": outcomes, **snapshot(graph, ignore)}
    finally:
        graph.close()
