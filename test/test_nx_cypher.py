"""Tests for NetworkXGraph.query() with read-only Cypher (cvcdocdb.nx_cypher).

The same small graph is loaded in every test:

    (Alice:Person {age: 30, city: Barcelona})-[:WORKS_AT {since: 2020}]->(Acme:Company)
    (Bob:Person   {age: 25, city: Girona})   -[:WORKS_AT {since: 2021}]->(Acme)
    (Carla:Person:Admin {age: 35, city: Barcelona})-[:WORKS_AT {since: 2019}]->(Beta:Company)
    (Dani:Person  {city: Girona})            — no age, no job
    (Alice)-[:KNOWS]->(Bob)-[:KNOWS]->(Carla)

``TestSameResultsAsNeo4j`` runs a battery of queries on both NetworkXGraph
and a real Neo4j and checks the results are identical.
"""

import os
import socket
import sys
import uuid
from urllib.parse import urlparse

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cvcdocdb.base import Node, Relation  # noqa: E402
from cvcdocdb.networkx_graph import NetworkXGraph  # noqa: E402


def _populate(graph, suffix=""):
    """Insert the test graph; labels get *suffix* (to isolate on Neo4j)."""
    person, company, admin = f"Person{suffix}", f"Company{suffix}", f"Admin{suffix}"
    people = {
        "Alice": Node(pk={"name": "Alice"}, main_label=person, age=30, city="Barcelona"),
        "Bob": Node(pk={"name": "Bob"}, main_label=person, age=25, city="Girona"),
        "Carla": Node(
            pk={"name": "Carla"}, main_label=person, alternative_labels=[admin],
            age=35, city="Barcelona",
        ),
        "Dani": Node(pk={"name": "Dani"}, main_label=person, city="Girona"),
    }
    companies = {
        "Acme": Node(pk={"name": "Acme"}, main_label=company, industry="Tech"),
        "Beta": Node(pk={"name": "Beta"}, main_label=company, industry="Retail"),
    }
    for node in [*people.values(), *companies.values()]:
        graph.insertNode(node, replace=True)
    for who, where, since in [("Alice", "Acme", 2020), ("Bob", "Acme", 2021), ("Carla", "Beta", 2019)]:
        graph.insertRelation(
            Relation(src=people[who], dst=companies[where], rel_type="WORKS_AT", since=since)
        )
    for a, b in [("Alice", "Bob"), ("Bob", "Carla")]:
        graph.insertRelation(Relation(src=people[a], dst=people[b], rel_type="KNOWS"))


@pytest.fixture
def g(tmp_path):
    graph = NetworkXGraph(persistence_path=str(tmp_path / "g.pkl"))
    _populate(graph)
    yield graph
    graph.close()


def _col(rows, key):
    return [r[key] for r in rows]


# ----------------------------------------------------------------------
# WHERE
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestWhere:
    def test_comparison(self, g):
        rows = g.query("MATCH (p:Person) WHERE p.age > 28 RETURN p.name AS name ORDER BY name")
        assert _col(rows, "name") == ["Alice", "Carla"]

    @pytest.mark.parametrize(
        "cond, expected",
        [
            ("p.age = 30", ["Alice"]),
            ("p.age <> 30", ["Bob", "Carla"]),
            ("p.age >= 30", ["Alice", "Carla"]),
            ("p.age <= 25", ["Bob"]),
            ("p.age < 30 OR p.city = 'Barcelona'", ["Alice", "Bob", "Carla"]),
            ("p.city = 'Barcelona' AND NOT p.age < 32", ["Carla"]),
            ("(p.age < 26 OR p.age > 34) AND p.city = 'Girona'", ["Bob"]),
            ("p.age IS NULL", ["Dani"]),
            ("p.age IS NOT NULL", ["Alice", "Bob", "Carla"]),
            ("p.name IN ['Bob', 'Dani', 'Zoe']", ["Bob", "Dani"]),
            ("p.name STARTS WITH 'Ca'", ["Carla"]),
            ("p.name ENDS WITH 'ce'", ["Alice"]),
            ("p.name CONTAINS 'an'", ["Dani"]),
            ("p.name =~ 'A.*|B.*'", ["Alice", "Bob"]),
            ("toLower(p.city) = 'girona'", ["Bob", "Dani"]),
            ("p.age + 5 > 34", ["Alice", "Carla"]),
        ],
    )
    def test_operators(self, g, cond, expected):
        rows = g.query(f"MATCH (p:Person) WHERE {cond} RETURN p.name AS name ORDER BY name")
        assert _col(rows, "name") == expected

    def test_parameters(self, g):
        rows = g.query(
            "MATCH (p:Person) WHERE p.age > $min AND p.city = $city RETURN p.name AS name",
            params={"min": 20, "city": "Girona"},
        )
        assert _col(rows, "name") == ["Bob"]

    def test_missing_parameter(self, g):
        with pytest.raises(ValueError, match="Missing parameter"):
            g.query("MATCH (p:Person) WHERE p.age > $min RETURN p")

    def test_parameter_value_is_not_injected_as_cypher(self, g):
        rows = g.query(
            "MATCH (p:Person {name: $name}) RETURN p.name AS name",
            params={"name": "x' OR 1=1 OR p.name = 'Alice"},
        )
        assert rows == []


# ----------------------------------------------------------------------
# Patterns
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestPatterns:
    def test_labelled_relationship(self, g):
        rows = g.query(
            "MATCH (p:Person)-[:WORKS_AT]->(c:Company) "
            "RETURN p.name AS p, c.name AS c ORDER BY p"
        )
        assert rows == [
            {"p": "Alice", "c": "Acme"},
            {"p": "Bob", "c": "Acme"},
            {"p": "Carla", "c": "Beta"},
        ]

    def test_incoming_direction(self, g):
        rows = g.query(
            "MATCH (c:Company {name: 'Acme'})<-[:WORKS_AT]-(p:Person) RETURN count(p) AS n"
        )
        assert rows == [{"n": 2}]

    def test_undirected(self, g):
        rows = g.query(
            "MATCH (a:Person {name: 'Bob'})-[:KNOWS]-(b) RETURN b.name AS name ORDER BY name"
        )
        assert _col(rows, "name") == ["Alice", "Carla"]

    def test_relationship_properties_and_variable(self, g):
        rows = g.query(
            "MATCH (p)-[r:WORKS_AT]->(:Company) WHERE r.since >= 2020 "
            "RETURN p.name AS name, r.since AS since ORDER BY since"
        )
        assert rows == [{"name": "Alice", "since": 2020}, {"name": "Bob", "since": 2021}]

    def test_relationship_inline_properties(self, g):
        rows = g.query("MATCH (p)-[:WORKS_AT {since: 2019}]->(c) RETURN p.name AS p, c.name AS c")
        assert rows == [{"p": "Carla", "c": "Beta"}]

    def test_type_alternatives(self, g):
        rows = g.query("MATCH (:Person {name: 'Bob'})-[r:KNOWS|WORKS_AT]->() RETURN type(r) AS t ORDER BY t")
        assert _col(rows, "t") == ["KNOWS", "WORKS_AT"]

    def test_any_relationship(self, g):
        rows = g.query("MATCH (:Person {name: 'Alice'})-[r]->(x) RETURN type(r) AS t, x.name AS x ORDER BY t")
        assert rows == [{"t": "KNOWS", "x": "Bob"}, {"t": "WORKS_AT", "x": "Acme"}]

    def test_chain(self, g):
        rows = g.query(
            "MATCH (a:Person)-[:KNOWS]->(b:Person)-[:WORKS_AT]->(c:Company) "
            "RETURN a.name AS a, c.name AS c ORDER BY a"
        )
        assert rows == [{"a": "Alice", "c": "Acme"}, {"a": "Bob", "c": "Beta"}]

    def test_relationship_uniqueness_within_a_match(self, g):
        rows = g.query(
            "MATCH (a:Person)-[:KNOWS]-(b)-[:KNOWS]-(c) RETURN a.name AS a, c.name AS c ORDER BY a"
        )
        assert rows == [{"a": "Alice", "c": "Carla"}, {"a": "Carla", "c": "Alice"}]

    def test_comma_separated_patterns(self, g):
        rows = g.query(
            "MATCH (a:Person {name: 'Alice'}), (c:Company {name: 'Beta'}) "
            "RETURN a.name AS a, c.name AS c"
        )
        assert rows == [{"a": "Alice", "c": "Beta"}]

    def test_shared_variable_across_matches(self, g):
        rows = g.query(
            "MATCH (a:Person)-[:WORKS_AT]->(c:Company) "
            "MATCH (b:Person)-[:WORKS_AT]->(c) WHERE a.name < b.name "
            "RETURN a.name AS a, b.name AS b"
        )
        assert rows == [{"a": "Alice", "b": "Bob"}]

    def test_alternative_label(self, g):
        assert _col(g.query("MATCH (p:Admin) RETURN p.name AS n"), "n") == ["Carla"]
        assert _col(g.query("MATCH (p:Person:Admin) RETURN p.name AS n"), "n") == ["Carla"]
        assert g.query("MATCH (p:Company:Admin) RETURN p.name AS n") == []

    def test_optional_match(self, g):
        rows = g.query(
            "MATCH (p:Person) OPTIONAL MATCH (p)-[:WORKS_AT]->(c:Company) "
            "RETURN p.name AS p, c.name AS c ORDER BY p"
        )
        assert rows == [
            {"p": "Alice", "c": "Acme"},
            {"p": "Bob", "c": "Acme"},
            {"p": "Carla", "c": "Beta"},
            {"p": "Dani", "c": None},
        ]

    def test_label_predicate_in_where(self, g):
        rows = g.query("MATCH (p) WHERE p:Admin RETURN p.name AS n")
        assert _col(rows, "n") == ["Carla"]


# ----------------------------------------------------------------------
# RETURN / WITH / aggregation / ordering
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestProjection:
    def test_grouped_count(self, g):
        rows = g.query(
            "MATCH (p:Person)-[:WORKS_AT]->(c:Company) "
            "RETURN c.name AS company, count(p) AS n ORDER BY n DESC, company"
        )
        assert rows == [{"company": "Acme", "n": 2}, {"company": "Beta", "n": 1}]

    def test_aggregates(self, g):
        [row] = g.query(
            "MATCH (p:Person) RETURN count(*) AS n, count(p.age) AS with_age, "
            "sum(p.age) AS s, avg(p.age) AS a, min(p.age) AS lo, max(p.age) AS hi"
        )
        assert row == {"n": 4, "with_age": 3, "s": 90, "a": 30.0, "lo": 25, "hi": 35}

    def test_count_distinct_and_collect(self, g):
        [row] = g.query(
            "MATCH (p:Person) RETURN count(DISTINCT p.city) AS cities, collect(p.name) AS names"
        )
        assert row["cities"] == 2
        assert sorted(row["names"]) == ["Alice", "Bob", "Carla", "Dani"]

    def test_aggregate_on_empty_match(self, g):
        assert g.query("MATCH (p:Nobody) RETURN count(p) AS n, sum(p.age) AS s") == [
            {"n": 0, "s": 0}
        ]
        assert g.query("MATCH (p:Nobody) RETURN p.name AS k, count(p) AS n") == []

    def test_distinct(self, g):
        rows = g.query("MATCH (p:Person) RETURN DISTINCT p.city AS city ORDER BY city")
        assert _col(rows, "city") == ["Barcelona", "Girona"]

    def test_order_skip_limit(self, g):
        rows = g.query(
            "MATCH (p:Person) WHERE p.age IS NOT NULL "
            "RETURN p.name AS name ORDER BY p.age DESC SKIP 1 LIMIT 1"
        )
        assert _col(rows, "name") == ["Alice"]

    def test_order_by_nulls_last_ascending(self, g):
        rows = g.query("MATCH (p:Person) RETURN p.name AS n, p.age AS a ORDER BY a")
        assert _col(rows, "n") == ["Bob", "Alice", "Carla", "Dani"]

    def test_with_aggregation_and_filter(self, g):
        rows = g.query(
            "MATCH (p:Person)-[:WORKS_AT]->(c:Company) "
            "WITH c, count(p) AS n WHERE n > 1 RETURN c.name AS name, n"
        )
        assert rows == [{"name": "Acme", "n": 2}]

    def test_with_then_match(self, g):
        rows = g.query(
            "MATCH (c:Company {name: 'Acme'}) WITH c "
            "MATCH (p)-[:WORKS_AT]->(c) RETURN p.name AS n ORDER BY n"
        )
        assert _col(rows, "n") == ["Alice", "Bob"]

    def test_functions(self, g):
        [row] = g.query(
            "MATCH (p:Person {name: 'Carla'})-[r]->(c:Company) RETURN "
            "labels(p) AS labels, type(r) AS t, toUpper(c.name) AS up, size(p.name) AS len, "
            "coalesce(p.missing, 'none') AS co, toString(p.age) AS s, keys(r) AS k"
        )
        assert sorted(row["labels"]) == ["Admin", "Person"]
        assert row["t"] == "WORKS_AT"
        assert row["up"] == "BETA"
        assert row["len"] == 5
        assert row["co"] == "none"
        assert row["s"] == "35"
        assert row["k"] == ["since"]

    def test_literals_and_arithmetic(self, g):
        assert g.query("RETURN 1 AS x") == [{"x": 1}]
        assert g.query("RETURN 2 * 3 + 1 AS x, 7 / 2 AS d, 7.0 / 2 AS f, 'a' + 'b' AS s") == [
            {"x": 7, "d": 3, "f": 3.5, "s": "ab"}
        ]

    def test_unaliased_return_uses_expression_text(self, g):
        rows = g.query("MATCH (p:Person {name: 'Bob'}) RETURN p.name, p.age")
        assert rows == [{"p.name": "Bob", "p.age": 25}]

    def test_node_and_relationship_values_keep_their_shape(self, g):
        [row] = g.query("MATCH (p:Person {name: 'Alice'})-[r:WORKS_AT]->(c) RETURN p, r")
        assert row["p"]["labels"] == ["Person"]
        assert row["p"]["properties"]["name"] == "Alice"
        assert row["r"]["type"] == "WORKS_AT"
        assert row["r"]["properties"] == {"since": 2020}
        assert {"start_node", "end_node"} <= set(row["r"])

    def test_return_star(self, g):
        [row] = g.query("MATCH (c:Company {name: 'Beta'}) RETURN *")
        assert row["c"]["properties"]["industry"] == "Retail"

    def test_keywords_are_case_insensitive(self, g):
        rows = g.query("match (p:Person) where p.age > 28 return p.name as n order by n desc limit 1")
        assert rows == [{"n": "Carla"}]


@pytest.mark.integration
class TestUnsupported:
    @pytest.mark.parametrize(
        "cypher",
        [
            "MATCH (a)-[*1..2]->(b) RETURN b",
            "MATCH p = (a)-[]->(b) RETURN p",
            "UNWIND [1, 2] AS x RETURN x",
            "MATCH (a) RETURN a UNION MATCH (b) RETURN b",
            "MATCH (p:Person) RETURN nosuchfunction(p.name) AS x",
        ],
    )
    def test_unsupported_syntax_raises_instead_of_wrong_results(self, g, cypher):
        with pytest.raises(ValueError):
            g.query(cypher)


@pytest.mark.integration
class TestWritesStillWork:
    """Write queries keep going through the existing engine."""

    def test_create_then_read(self, g):
        g.query("CREATE (n:TestNode {id: 1, label: 'x'})")
        assert g.query("MATCH (n:TestNode) WHERE n.id = 1 RETURN n.label AS l") == [{"l": "x"}]


# ----------------------------------------------------------------------
# Differential test against a real Neo4j
# ----------------------------------------------------------------------

DIFFERENTIAL_QUERIES = [
    "MATCH (p:Person) WHERE p.age > 28 RETURN p.name AS name ORDER BY name",
    "MATCH (p:Person) WHERE p.age IS NULL OR p.city = 'Barcelona' RETURN p.name AS name ORDER BY name",
    "MATCH (p:Person) WHERE p.name IN ['Bob', 'Dani'] AND NOT p.city STARTS WITH 'B' RETURN p.name AS name ORDER BY name",
    "MATCH (p:Person)-[:WORKS_AT]->(c:Company) RETURN p.name AS p, c.name AS c ORDER BY p",
    "MATCH (c:Company)<-[r:WORKS_AT]-(p) RETURN c.name AS c, count(p) AS n, min(r.since) AS first ORDER BY c",
    "MATCH (a:Person {name: 'Bob'})-[:KNOWS]-(b) RETURN b.name AS name ORDER BY name",
    "MATCH (a:Person)-[:KNOWS]-(b)-[:KNOWS]-(c) RETURN a.name AS a, c.name AS c ORDER BY a",
    "MATCH (a:Person)-[:KNOWS]->(b:Person)-[:WORKS_AT]->(c:Company) RETURN a.name AS a, c.name AS c ORDER BY a",
    "MATCH (p:Person) RETURN count(*) AS n, count(p.age) AS k, sum(p.age) AS s, avg(p.age) AS a, max(p.age) AS hi",
    "MATCH (p:Person) RETURN count(DISTINCT p.city) AS cities",
    "MATCH (p:Person) RETURN DISTINCT p.city AS city ORDER BY city",
    "MATCH (p:Person) RETURN p.name AS name ORDER BY p.age DESC SKIP 1 LIMIT 2",
    "MATCH (p:Person) OPTIONAL MATCH (p)-[:WORKS_AT]->(c:Company) RETURN p.name AS p, c.name AS c ORDER BY p",
    "MATCH (p:Person)-[:WORKS_AT]->(c:Company) WITH c, count(p) AS n WHERE n > 1 RETURN c.name AS name, n",
    "MATCH (p:Admin) RETURN p.name AS name",
    "MATCH (p:Person) WHERE toLower(p.city) = 'girona' RETURN p.name AS name, coalesce(p.age, -1) AS age ORDER BY name",
    "MATCH (p:Nobody) RETURN count(p) AS n",
]


def _neo4j_config():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        from dotenv import load_dotenv

        load_dotenv(env_path)
    target = os.environ.get("NEO4J_TARGET", "DEV").upper()

    def _env(key):
        return os.environ.get(f"NEO4J_{target}_{key}") or os.environ.get(f"NEO4J_{key}")

    if not (_env("URL") and _env("USER") and _env("PASSWORD")):
        return None
    parsed = urlparse(_env("URL"))
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 7687), 0.5):
            pass
    except OSError:
        return None
    return dict(url=_env("URL"), user=_env("USER"), password=_env("PASSWORD"), database=_env("DATABASE"))


@pytest.fixture(scope="module")
def both_backends(tmp_path_factory):
    config = _neo4j_config()
    if config is None:
        pytest.skip("Neo4j not reachable")
    from cvcdocdb.neo4j_graph import Neo4jGraph

    suffix = uuid.uuid4().hex[:8]
    neo = Neo4jGraph(**config)
    nx_graph = NetworkXGraph(persistence_path=str(tmp_path_factory.mktemp("diff") / "g.pkl"))
    _populate(neo, suffix)
    _populate(nx_graph, suffix)
    try:
        yield nx_graph, neo, suffix
    finally:
        neo.query(
            f"MATCH (n) WHERE n:Person{suffix} OR n:Company{suffix} DETACH DELETE n"
        )
        neo.close()
        nx_graph.close()


@pytest.mark.slow
@pytest.mark.parametrize("cypher", DIFFERENTIAL_QUERIES)
def test_same_results_as_neo4j(both_backends, cypher):
    nx_graph, neo, suffix = both_backends
    for label in ("Person", "Company", "Admin", "Nobody"):
        cypher = cypher.replace(f":{label}", f":{label}{suffix}")
    assert nx_graph.query(cypher) == neo.query(cypher)
