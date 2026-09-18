"""Demo: full Neo4j workflow with propagation properties.

Connects to a Neo4j DEV instance, loads the Game of Thrones dataset,
generates YAML schema + Python classes, initializes propagation
properties on existing nodes/edges, and runs sample queries.

Usage::

    # Run against local Neo4j DEV
    python -m cvcdocdb.exemples.demo_propagation

    # Or with explicit connection parameters via env vars:
    NEO4J_DEV_URL=bolt://dev.example.com:7687 \\
    NEO4J_DEV_USER=neo4j \\
    NEO4J_DEV_PASSWORD=devpass \\
    python -m cvcdocdb.exemples.demo_propagation
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict

from cvcdocdb import Neo4jGraph, NetworkXGraph
from cvcdocdb.exemples import load_got_characters
from cvcdocdb.graph_store import GraphStore
from cvcdocdb.schema_gen import generate_classes
from cvcdocdb.rdf_schema import convert_rdf_to_yaml


# ======================================================================
# Helpers
# ======================================================================

def neo4j_config(default_target: str = "DEV") -> Dict[str, str]:
    """Build Neo4j connection config from environment variables.

    Args:
        default_target: Default target name (DEV, TEST, PROD).

    Returns:
        Dict with keys: target, url, user, password, database.
    """
    target = os.environ.get("NEO4J_TARGET", default_target).upper()
    prefix = f"NEO4J_{target}_"
    return {
        "target": target,
        "url": os.environ.get(f"{prefix}URL", os.environ.get("NEO4J_URL", "bolt://localhost:7687")),
        "user": os.environ.get(f"{prefix}USER", os.environ.get("NEO4J_USER", "neo4j")),
        "password": os.environ.get(f"{prefix}PASSWORD", os.environ.get("NEO4J_PASSWORD", "secret")),
        "database": os.environ.get(f"{prefix}DATABASE", os.environ.get("NEO4J_DATABASE", "neo4j")),
    }


def section(title: str) -> None:
    """Print a section separator."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def sub_section(title: str) -> None:
    """Print a subsection separator."""
    print(f"\n--- {title} ---")


# ======================================================================
# Step 1: Connect to Neo4j
# ======================================================================

def step1_connect(cfg: Dict[str, str]) -> Neo4jGraph:
    """Connect to Neo4j and verify the database exists.

    Args:
        cfg: Neo4j connection config dict.

    Returns:
        Connected Neo4jGraph instance.
    """
    section("Step 1: Connect to Neo4j")

    print(f"  Target:  {cfg['target']}")
    print(f"  URL:     {cfg['url']}")
    print(f"  User:    {cfg['user']}")
    print(f"  Database: {cfg['database']}")

    graph = Neo4jGraph(
        url=cfg["url"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
    )

    # Verify connection by running a simple query
    result = graph.query("RETURN 1 AS test")
    if result and result[0].get("test") == 1:
        print("  ✅ Connection verified.")
    else:
        print("  ⚠️  Connection returned unexpected result.")

    # Check if the database has any data
    count_result = graph.query("MATCH (n) RETURN count(n) AS total")
    total = count_result[0]["total"] if count_result else 0
    print(f"  Nodes in database: {total}")

    if total == 0:
        print("  ℹ️  Database is empty — will load GOT characters.")

    return graph


# ======================================================================
# Step 2: Load GOT dataset
# ======================================================================

def step2_load_got(graph: Neo4jGraph) -> Dict[str, int]:
    """Load Game of Thrones characters into the database.

    Args:
        graph: Connected Neo4jGraph instance.

    Returns:
        Dict with insertion counters.
    """
    section("Step 2: Load Game of Thrones dataset")

    # Load from thronesapi.com (with fallback data)
    stats = load_got_characters(graph, limit=25)
    print(f"  Loaded: {json.dumps(stats, indent=4)}")

    # Verify
    result = graph.query("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS cnt ORDER BY cnt DESC")
    print("\n  Nodes by label:")
    for row in result:
        print(f"    {row['label']}: {row['cnt']}")

    result = graph.query(
        "MATCH ()-[r]->() RETURN type(r) AS rel_type, count(r) AS cnt ORDER BY cnt DESC"
    )
    print("\n  Relations by type:")
    for row in result:
        print(f"    {row['rel_type']}: {row['cnt']}")

    return stats


# ======================================================================
# Step 3: Generate YAML schema
# ======================================================================

def step3_generate_yaml(graph: Neo4jGraph, output_dir: Path) -> str:
    """Generate YAML schema from the Neo4j database.

    Args:
        graph: Connected Neo4jGraph instance.
        output_dir: Directory to write the YAML file.

    Returns:
        Path to the generated YAML file.
    """
    section("Step 3: Generate YAML schema")

    yaml_content = graph.schema_yaml(db_name="got")

    yaml_path = output_dir / "got_schema.yaml"
    yaml_path.write_text(yaml_content)
    print(f"  Written: {yaml_path}")
    print(f"  Size:    {len(yaml_content)} bytes")

    print("\n  Schema preview:")
    for line in yaml_content.split("\n")[:15]:
        print(f"    {line}")
    print("    ...")

    return str(yaml_path)


# ======================================================================
# Step 4: Generate Python classes
# ======================================================================

def step4_generate_classes(yaml_path: str, output_dir: Path) -> Path:
    """Generate Python entity classes from the YAML schema.

    Args:
        yaml_path: Path to the YAML schema file.
        output_dir: Directory to write the generated Python file.

    Returns:
        Path to the generated Python file.
    """
    section("Step 4: Generate Python classes")

    code = generate_classes(yaml_path)

    py_path = output_dir / "got_entities.py"
    py_path.write_text(code)
    print(f"  Written: {py_path}")
    print(f"  Size:    {len(code)} bytes")

    # Show first few lines
    print("\n  Generated code preview:")
    for line in code.split("\n")[:20]:
        print(f"    {line}")
    print("    ...")

    return py_path


# ======================================================================
# Step 5: Initialize propagation properties
# ======================================================================

def step5_init_propagation(graph: Neo4jGraph) -> None:
    """Initialize propagation properties on existing nodes and edges.

    This scans the entire graph, identifies WeakNodes and WeakRelations
    from the edge structure, and sets the corresponding properties.

    Args:
        graph: Connected Neo4jGraph instance.
    """
    section("Step 5: Initialize propagation properties")

    # Check current state
    result = graph.query(
        "MATCH (n) RETURN count(n) AS total, "
        "count(n.is_weak) AS weak_count, "
        "count(n._weak_init_done) AS done_count"
    )
    row = result[0] if result else {}
    print(f"  Before init:")
    print(f"    Total nodes:      {row.get('total', 0)}")
    print(f"    Nodes with is_weak: {row.get('weak_count', 0)}")
    print(f"    Nodes with _weak_init_done: {row.get('done_count', 0)}")

    # Run initialization (synchronous)
    print("\n  Running init_propagation()...")
    result = graph.init_propagation()
    print(f"  Result: {result}")

    # Check state after init
    result = graph.query(
        "MATCH (n) RETURN count(n) AS total, "
        "count(n.is_weak) AS weak_count, "
        "count(n._weak_init_done) AS done_count"
    )
    row = result[0] if result else {}
    print(f"\n  After init:")
    print(f"    Total nodes:      {row.get('total', 0)}")
    print(f"    Nodes with is_weak: {row.get('weak_count', 0)}")
    print(f"    Nodes with _weak_init_done: {row.get('done_count', 0)}")

    # Show propagation edges
    result = graph.query(
        "MATCH ()-[r]->() WHERE r._propagate = TRUE "
        "RETURN count(r) AS propagate_edges"
    )
    prop_count = result[0]["propagate_edges"] if result else 0
    print(f"    Edges with _propagate: {prop_count}")


# ======================================================================
# Step 6: Demonstrate create_group
# ======================================================================

def step6_create_group(graph: Neo4jGraph) -> None:
    """Demonstrate transactional group creation with create_group().

    Creates a strong node (Document) with WeakNodes (Section, Page)
    in a single atomic transaction.

    Args:
        graph: Connected Neo4jGraph instance.
    """
    section("Step 6: Transactional group creation")

    from cvcdocdb.base import Node, WeakNode

    # Create a Document with sections and pages
    doc = Node(
        pk={"doc_id": "GOT-DEMO-001"},
        main_label="Document",
        title="Game of Thrones — Episode Guide",
        author="Demo Script",
    )

    section1 = WeakNode(
        parent=doc,
        parent_relation="HAS_SECTION",
        pk={"section": "intro"},
        main_label="Section",
        title="Introduction",
        order=1,
    )

    section2 = WeakNode(
        parent=doc,
        parent_relation="HAS_SECTION",
        pk={"section": "characters"},
        main_label="Section",
        title="Main Characters",
        order=2,
    )

    page1 = WeakNode(
        parent=section1,
        parent_relation="HAS_PAGE",
        pk={"page": 1},
        main_label="Page",
        content="Welcome to the Game of Thrones episode guide.",
        order=1,
    )

    page2 = WeakNode(
        parent=section2,
        parent_relation="HAS_PAGE",
        pk={"page": 1},
        main_label="Page",
        content="Jon Snow, Daenerys Targaryen, Tyrion Lannister...",
        order=1,
    )

    print("  Creating group:")
    print(f"    Strong node: Document(GOT-DEMO-001)")
    print(f"    Weak nodes:  Section(intro), Section(characters)")
    print(f"    Weak nodes:  Page(1) → intro, Page(1) → characters")

    doc_id = graph.create_group(
        strong_node=doc,
        weak_nodes=[section1, section2, page1, page2],
    )

    print(f"\n  ✅ Group created (strong node id: {doc_id})")

    # Verify the group
    result = graph.query(
        "MATCH (n) WHERE n.doc_id = 'GOT-DEMO-001' "
        "RETURN labels(n) AS labels, n.title, n._weak_init_done"
    )
    print("\n  Document node:")
    for row in result:
        print(f"    Labels: {row['labels']}, Title: {row['title']}, "
              f"_weak_init_done: {row.get('_weak_init_done')}")

    result = graph.query(
        "MATCH (n:Section) WHERE n._weak_init_done IS NULL "
        "RETURN labels(n) AS labels, n.title, n.is_weak, n._propagate"
    )
    print("\n  Section nodes (after init_propagation will mark them):")
    for row in result:
        print(f"    {row['title']}: is_weak={row.get('is_weak')}, "
              f"_propagate={row.get('_propagate')}")


# ======================================================================
# Step 7: Run sample queries
# ======================================================================

def step7_queries(graph: Neo4jGraph) -> None:
    """Run sample queries to demonstrate the initialized graph.

    Args:
        graph: Connected Neo4jGraph instance.
    """
    section("Step 7: Sample queries")

    # Query 1: All characters and their houses
    sub_section("Characters by House")
    result = graph.query(
        "MATCH (c:Character)-[:MEMBER_OF]->(h:House) "
        "RETURN h.name AS house, collect(c.name) AS characters "
        "ORDER BY house"
    )
    for row in result:
        print(f"  {row['house']}: {', '.join(row['characters'][:5])}")
        if len(row['characters']) > 5:
            print(f"    ... and {len(row['characters']) - 5} more")

    # Query 2: Nodes with propagation properties
    sub_section("Nodes with propagation properties")
    result = graph.query(
        "MATCH (n) WHERE n.is_weak = TRUE "
        "RETURN labels(n) AS label, count(n) AS count "
        "ORDER BY count DESC"
    )
    for row in result:
        print(f"  {row['label']}: {row['count']} nodes")

    # Query 3: Edges with _propagate
    sub_section("Edges with _propagate")
    result = graph.query(
        "MATCH (a)-[r]->(b) WHERE r._propagate = TRUE "
        "RETURN type(a) AS from_label, type(r) AS rel_type, "
        "type(b) AS to_label, count(*) AS count "
        "ORDER BY count DESC"
    )
    for row in result:
        print(f"  {row['from_label']} -[{row['rel_type']}]-> {row['to_label']}: {row['count']}")

    # Query 4: Document group with its weak children
    sub_section("Document group hierarchy")
    result = graph.query(
        "MATCH path = (d:Document {doc_id: 'GOT-DEMO-001'})-[*1..3]->(n) "
        "RETURN path"
    )
    if result:
        print("  Path from Document to its children:")
        for row in result:
            path = row.get("path")
            if path:
                nodes = path.nodes
                rels = path.relationships
                parts = []
                for i, node in enumerate(nodes):
                    label = list(node.labels)[0] if hasattr(node, 'labels') else 'Node'
                    props = dict(node)
                    name = props.get('title', props.get('name', str(node.element_id)))
                    parts.append(f"{label}({name})")
                for rel in rels:
                    parts.append(f"-[{rel.type}]->")
                print(f"    {' → '.join(parts)}")

    # Query 5: Summary statistics
    sub_section("Summary statistics")
    result = graph.query(
        "MATCH (n) "
        "RETURN "
        "  count(n) AS total_nodes, "
        "  count(n.is_weak) AS weak_nodes, "
        "  count(n._weak_init_done) AS initialized_nodes "
    )
    row = result[0] if result else {}
    print(f"  Total nodes:          {row.get('total_nodes', 0)}")
    print(f"  Weak nodes:           {row.get('weak_nodes', 0)}")
    print(f"  Initialized nodes:    {row.get('initialized_nodes', 0)}")

    result = graph.query(
        "MATCH ()-[r]->() "
        "RETURN count(r) AS total_edges, "
        "count(r._propagate) AS propagate_edges "
    )
    row = result[0] if result else {}
    print(f"  Total edges:          {row.get('total_edges', 0)}")
    print(f"  Propagate edges:      {row.get('propagate_edges', 0)}")


# ======================================================================
# Main
# ======================================================================

def main() -> int:
    """Run the full demo workflow."""
    print(textwrap.dedent("""
    ╔══════════════════════════════════════════════════════════╗
    ║  cvcdocdb — Neo4j Propagation Demo                      ║
    ║  Full workflow: connect → load → generate → init → query ║
    ╚══════════════════════════════════════════════════════════╝
    """))

    # Get connection config
    cfg = neo4j_config()

    # Determine output directory for generated files
    output_dir = Path(__file__).parent / "generated"
    output_dir.mkdir(exist_ok=True)

    try:
        # Step 1: Connect
        graph = step1_connect(cfg)

        # Step 2: Load GOT dataset
        step2_load_got(graph)

        # Step 3: Generate YAML schema
        yaml_path = step3_generate_yaml(graph, output_dir)

        # Step 4: Generate Python classes
        py_path = step4_generate_classes(yaml_path, output_dir)

        # Step 5: Initialize propagation properties
        step5_init_propagation(graph)

        # Step 6: Demonstrate create_group
        step6_create_group(graph)

        # Step 7: Run sample queries
        step7_queries(graph)

        print(f"\n{'=' * 70}")
        print("  ✅ Demo completed successfully!")
        print(f"{'=' * 70}")
        print(f"\n  Generated files:")
        print(f"    {yaml_path}")
        print(f"    {py_path}")
        print(f"\n  To run again with a different Neo4j instance, set:")
        print(f"    NEO4J_DEV_URL=bolt://your-host:7687")
        print(f"    NEO4J_DEV_USER=your-user")
        print(f"    NEO4J_DEV_PASSWORD=your-password")

        return 0

    except Exception as exc:
        print(f"\n❌ Error: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    finally:
        if 'graph' in locals():
            graph.close()


if __name__ == "__main__":
    raise SystemExit(main())
