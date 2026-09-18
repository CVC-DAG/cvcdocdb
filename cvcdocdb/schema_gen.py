"""Generate Python entity classes from a YAML schema.

Parses the YAML output of ``GraphStore.schema_yaml()`` and generates
Python source code with class definitions for every label, relationship,
and weak relation found in the schema.

Example usage::

    from cvcdocdb.networkx_graph import NetworkXGraph
    from cvcdocdb.schema_gen import generate_classes, generate_file

    g = NetworkXGraph(persistence_path="my_graph.pkl")
    source = generate_classes(g.schema_yaml("my_db"))

    # Or write directly to a file:
    generate_file(g, "my_db", output_dir="entities/")

Output structure::

    from cvcdocdb.base import Node, WeakNode, Relation, WeakRelation

    class Character(Node):
        \"\"\"Auto-generated from schema.\"\"\"

        def __init__(self, pk, **kwargs):
            super().__init__(pk=pk, main_label="Character", **kwargs)
            self.house = kwargs.get("house", "")
            self.name = kwargs.get("name", "")

    class Section(WeakNode):
        \"\"\"Auto-generated from schema.\"\"\"

        def __init__(self, parent, **kwargs):
            super().__init__(parent=parent, main_label="Section",
                             parent_relation="HAS_SECTION", **kwargs)
            self.title = kwargs.get("title", "")

    class Knows(Relation):
        \"\"\"Auto-generated from schema.\"\"\"

        def __init__(self, src, dst, **kwargs):
            super().__init__(src=src, dst=dst, rel_type="KNOWS", **kwargs)

    class HasSection(WeakRelation):
        \"\"\"Auto-generated from schema.\"\"\"

        def __init__(self, src, dst, **kwargs):
            super().__init__(src=src, dst=dst, rel_type="HAS_SECTION",
                             propagate=True, **kwargs)
"""

from __future__ import annotations

import datetime
import os
import re
from typing import Any, Dict, List, Optional

# Schema-derived names (class names, property names) become literal Python
# identifiers in the generated source (`class {name}(Node):`,
# `self.{name} = ...`). An untrusted schema (e.g. converted from a
# third-party RDF/OWL ontology) must not be able to smuggle arbitrary
# code through these positions.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(name: str, kind: str) -> str:
    """Ensure a schema-derived name is safe to splice as a Python identifier.

    Raises:
        ValueError: If `name` is not a valid Python identifier.
    """
    if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid {kind} {name!r}: schema-derived {kind}s must match "
            f"{_IDENTIFIER_RE.pattern!r} to be used safely in generated code."
        )
    return name

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from .networkx_graph import NetworkXGraph


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_classes(yaml_source: str) -> str:
    """Generate Python entity classes from a YAML schema string.

    Args:
        yaml_source: The YAML string returned by
            ``GraphStore.schema_yaml(db_name)`` or ``rdf_to_yaml()``.

    Returns:
        A Python source string containing all generated classes.
    """
    if yaml is None:
        raise ImportError(
            "PyYAML is required for schema generation. "
            "Install it with: pip install pyyaml"
        )

    data = yaml.safe_load(yaml_source)
    lines: List[str] = []

    # Header
    lines.append('"""Auto-generated entity classes from schema."""')
    lines.append("")
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("from typing import Any, Dict, List, Optional")
    lines.append("")
    lines.append("from cvcdocdb.base import Node, WeakNode, Relation, WeakRelation")
    lines.append("")
    lines.append("")

    # ── Node / WeakNode classes ────────────────────────────────────
    labels = data.get("labels", {}) or {}
    for label_name in sorted(labels):
        info = labels[label_name]
        base_class = info.get("base_class", "Node")
        props = info.get("properties", {}) or {}
        class_name = info.get("class_name", label_name)
        primary_key: List[str] = info.get("primary_key", []) or []
        doc = info.get("doc", f"Auto-generated entity class.")

        if base_class == "WeakNode":
            lines.append(_generate_weaknode_class(
                class_name, label_name, props,
                info.get("parent"),
                info.get("parent_relation"),
                primary_key,
                doc,
            ))
        else:
            lines.append(_generate_node_class(
                class_name, label_name, props,
                primary_key,
                doc,
            ))

    # ── Regular Relation classes ───────────────────────────────────
    rels = data.get("relationships", {}) or {}
    for rel_name in sorted(rels):
        info = rels[rel_name]
        # Skip if this is a WeakRelation (already in weak_relations)
        wr = data.get("weak_relations", {}) or {}
        if rel_name in wr:
            continue
        class_name = info.get("class_name", rel_name)
        lines.append(_generate_relation_class(
            class_name, rel_name, info.get("properties", {}) or {},
        ))

    # ── WeakRelation classes ───────────────────────────────────────
    wrs = data.get("weak_relations", {}) or {}
    for rel_name in sorted(wrs):
        info = wrs[rel_name]
        class_name = info.get("class_name", rel_name)
        propagate = info.get("propagate", True)
        lines.append(_generate_weakrelation_class(
            class_name, rel_name, propagate,
        ))

    return "\n".join(lines) + "\n"


def generate_file(
    graph: NetworkXGraph,
    db_name: str,
    output_dir: str,
    filename: Optional[str] = None,
) -> str:
    """Generate Python entity classes and write them to a file.

    Args:
        graph: A graph store instance (NetworkXGraph or Neo4jGraph).
        db_name: Database name used for schema introspection.
        output_dir: Directory where the .py file will be written.
        filename: Output filename (default: ``entities_{db_name}.py``).

    Returns:
        The absolute path to the generated file.
    """
    if filename is None:
        filename = f"entities_{db_name}.py"

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, filename)

    yaml_source = graph.schema_yaml(db_name)
    source = generate_classes(yaml_source)

    with open(out_path, "w") as f:
        f.write(source)

    return os.path.abspath(out_path)


# ---------------------------------------------------------------------------
# Class generators
# ---------------------------------------------------------------------------


def _generate_node_class(
    class_name: str,
    label: str,
    props: Dict[str, str],
    primary_key: List[str],
    doc: str,
) -> str:
    """Generate a Node subclass."""
    _validate_identifier(class_name, "class_name")
    lines: List[str] = []
    lines.append(f"class {class_name}(Node):")
    lines.append(f"    {doc + '.'!r}")
    lines.append("")

    # pk is optional when no primary_key defined
    pk_type = "Dict[str, Any]" if primary_key else "Optional[Dict[str, Any]]"
    pk_default = f" = None" if not primary_key else ""
    lines.append(f"    def __init__(self, pk: {pk_type}{pk_default}, **kwargs: Any) -> None:")
    if primary_key:
        pk_doc = f"pk: Primary key dict with fields: {', '.join(primary_key)}."
    else:
        pk_doc = "pk: Optional primary key dict. When not provided, the backend assigns an ID."
    init_doc = (
        f"Initialize a {class_name} node.\n\n"
        f"        Args:\n"
        f"            {pk_doc}\n"
        f"            **kwargs: Additional properties and attributes."
    )
    lines.append(f"        {init_doc!r}")

    # Call super().__init__
    init_kwargs = [f"pk=pk", f"main_label={label!r}"]
    lines.append("        super().__init__(" + ", ".join(init_kwargs) + ", **kwargs)")

    # Set properties
    for prop_name in sorted(props):
        _validate_identifier(prop_name, "property name")
        lines.append(f"        self.{prop_name} = kwargs.get({prop_name!r}, {props[prop_name]!r})")

    return "\n".join(lines)


def _generate_weaknode_class(
    class_name: str,
    label: str,
    props: Dict[str, str],
    parent_label: Optional[str],
    parent_relation: Optional[str],
    primary_key: List[str],
    doc: str,
) -> str:
    """Generate a WeakNode subclass."""
    _validate_identifier(class_name, "class_name")
    lines: List[str] = []
    lines.append(f"class {class_name}(WeakNode):")
    lines.append(f"    {doc + '.'!r}")
    lines.append("")
    lines.append(f"    def __init__(self, parent: Node, **kwargs: Any) -> None:")
    init_doc = (
        f"Initialize a {class_name} weak node.\n\n"
        f"        Args:\n"
        f"            parent: The parent {parent_label or 'Node'} instance.\n"
        f"            **kwargs: Additional properties and attributes."
    )
    lines.append(f"        {init_doc!r}")

    # Call super().__init__
    init_kwargs = [f"parent=parent", f"main_label={label!r}"]
    if parent_relation:
        init_kwargs.append(f"parent_relation={parent_relation!r}")
    lines.append("        super().__init__(" + ", ".join(init_kwargs) + ", **kwargs)")

    # Set properties
    for prop_name in sorted(props):
        _validate_identifier(prop_name, "property name")
        lines.append(f"        self.{prop_name} = kwargs.get({prop_name!r}, {props[prop_name]!r})")

    return "\n".join(lines)


def _generate_relation_class(
    class_name: str,
    rel_type: str,
    props: Dict[str, str],
) -> str:
    """Generate a Relation subclass."""
    _validate_identifier(class_name, "class_name")
    lines: List[str] = []
    lines.append(f"class {class_name}(Relation):")
    lines.append(f"    'Auto-generated relation class.'")
    lines.append("")
    lines.append(f"    def __init__(self, src: Node, dst: Node, **kwargs: Any) -> None:")
    init_doc = (
        f"Initialize a {class_name} relation.\n\n"
        f"        Args:\n"
        f"            src: Source node.\n"
        f"            dst: Destination node.\n"
        f"            **kwargs: Edge properties."
    )
    lines.append(f"        {init_doc!r}")

    init_kwargs = [f"src=src", f"dst=dst", f"rel_type={rel_type!r}"]
    lines.append("        super().__init__(" + ", ".join(init_kwargs) + ", **kwargs)")

    # Set properties
    for prop_name in sorted(props):
        _validate_identifier(prop_name, "property name")
        lines.append(f"        self.{prop_name} = kwargs.get({prop_name!r}, {props[prop_name]!r})")

    return "\n".join(lines)


def _generate_weakrelation_class(
    class_name: str,
    rel_type: str,
    propagate: bool = True,
) -> str:
    """Generate a WeakRelation subclass.

    Args:
        class_name: The name of the class to generate.
        rel_type: The relation type (e.g. ``"HAS_PAGE"``).
        propagate: Whether the relation carries the ``_propagate=TRUE``
            flag for cascade delete (default ``True``).
    """
    _validate_identifier(class_name, "class_name")
    lines: List[str] = []
    lines.append(f"class {class_name}(WeakRelation):")
    class_doc = f"Auto-generated weak relation class.\n\n    Propagate: {propagate}."
    lines.append(f"    {class_doc!r}")
    lines.append("")
    lines.append(f"    def __init__(self, src: Node, dst: Node, **kwargs: Any) -> None:")
    init_doc = (
        f"Initialize a {class_name} weak relation.\n\n"
        f"        Args:\n"
        f"            src: Source (parent) node.\n"
        f"            dst: Destination (child) node.\n"
        f"            propagate: Override the default propagation flag.\n"
        f"            **kwargs: Edge properties."
    )
    lines.append(f"        {init_doc!r}")

    init_kwargs = [f"src=src", f"dst=dst", f"rel_type={rel_type!r}"]
    if propagate:
        init_kwargs.append(f"propagate={propagate}")
    lines.append("        super().__init__(" + ", ".join(init_kwargs) + ", **kwargs)")

    return "\n".join(lines)
