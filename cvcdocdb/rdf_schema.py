"""Convert RDF/OWL ontology to CVCDocDB YAML schema.

Parses an RDF file (Turtle, RDF/XML, N-Triples, etc.) and produces a YAML
schema compatible with ``cvcdocdb.schema_gen.generate_classes()``.

Ontology features mapped:

- ``owl:Class`` / ``rdfs:Class`` → Node label
- ``rdfs:subClassOf`` → WeakNode hierarchy (parent)
- ``owl:DatatypeProperty`` → Node properties (``rdfs:domain``)
- ``owl:ObjectProperty`` → Relationships (``rdfs:domain`` / ``range``)
- ``owl:hasKey`` → Primary key fields
- ``owl:uniqueIdentity`` → Unique identifier fields
- ``rdfs:range`` → Relationship destination label
- ``rdfs:comment`` → Class docstring

Usage::

    from cvcdocdb.rdf_schema import download_ontology, rdf_to_yaml

    # Download and convert in one step:
    yaml_str = download_ontology_and_convert(
        "https://raw.githubusercontent.com/ICA-EGAD/RiC-O/master/ontology/current-version/RiC-O_1-1.rdf",
        "rico",
        output_dir="cvcdocdb/"
    )

    # Or download first, then convert:
    path = download_ontology(url, output_dir="ontologies/")
    yaml_str = rdf_to_yaml(path, "my_db")

Supported input formats:
    Turtle (.ttl), RDF/XML (.rdf), N-Triples (.nt), N-Quads (.nq),
    TriX (.trix), Trig (.trig), JSON-LD (.jsonld).
"""

from __future__ import annotations

import datetime
import os
import urllib.request
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import rdflib
    from rdflib import RDF, RDFS, OWL, Graph, URIRef, Literal, Namespace
except ImportError:  # pragma: no cover
    raise ImportError(
        "rdflib is required for RDF ontology support. "
        "Install it with: pip install rdflib"
    )

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# URIs for OWL constructs not in rdflib's OWL namespace
OWL_HAS_KEY = URIRef("http://www.w3.org/2002/07/owl#hasKey")
OWL_UNIQUE_IDENTITY = URIRef("http://www.w3.org/2002/07/owl#uniqueIdentity")
OWL_CLASS = URIRef("http://www.w3.org/2002/07/owl#Class")
RDFS_CLASS = URIRef("http://www.w3.org/2000/01/rdf-schema#Class")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def download_ontology(
    url: str,
    output_dir: str = "ontologies",
    filename: Optional[str] = None,
) -> str:
    """Download an RDF ontology from a URL and save it locally.

    Args:
        url: The URL of the RDF file (RDF/XML, Turtle, etc.).
        output_dir: Directory where the file will be saved.
        filename: Output filename (default: derived from URL path).

    Returns:
        The absolute path to the downloaded file.
    """
    os.makedirs(output_dir, exist_ok=True)

    if filename is None:
        # Extract filename from URL path
        basename = url.rstrip("/").split("/")[-1]
        if "." not in basename:
            basename = "ontology.rdf"
        filename = basename

    out_path = os.path.join(output_dir, filename)

    req = urllib.request.Request(url, headers={"User-Agent": "cvcdocdb-rdf/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()

    with open(out_path, "wb") as f:
        f.write(data)

    return os.path.abspath(out_path)


def download_ontology_and_convert(
    url: str,
    db_name: str,
    output_dir: str = "cvcdocdb",
    filename: Optional[str] = None,
    ontology_ns: Optional[str] = None,
) -> str:
    """Download an RDF ontology, convert to YAML, and generate Python classes.

    Convenience function that downloads the ontology, converts it to CVCDocDB YAML
    schema, and writes the Python entity classes to a file.

    Args:
        url: The URL of the RDF file.
        db_name: Database name for the YAML header.
        output_dir: Directory where the Python file will be saved.
        filename: Output Python filename (default: ``entities_{db_name}.py``).
        ontology_ns: The ontology namespace URI (e.g.
            ``"https://www.ica.org/standards/RiC/ontology#"``).
            If not provided, the namespace is auto-detected from the ontology.

    Returns:
        The absolute path to the generated Python file.
    """
    # Step 1: Download
    ont_path = download_ontology(url, output_dir="ontologies", filename=filename)

    # Step 2: Convert to YAML
    yaml_str = rdf_to_yaml(ont_path, db_name, ontology_ns=ontology_ns)

    # Step 3: Generate Python classes
    py_source = generate_classes(yaml_str)

    # Step 4: Write file
    if filename is None:
        py_filename = f"{db_name}_entities.py"
    else:
        py_filename = filename

    out_path = os.path.join(output_dir, py_filename)
    os.makedirs(output_dir, exist_ok=True)

    with open(out_path, "w") as f:
        f.write(py_source)

    return os.path.abspath(out_path)


def rdf_to_yaml(
    source: str,
    db_name: str,
    ontology_ns: Optional[str] = None,
) -> str:
    """Convert an RDF/OWL ontology to a CVCDocDB YAML schema.

    Args:
        source: Path to the RDF file or a URL.
        db_name: Human-readable database name for the YAML header.
        ontology_ns: The ontology namespace URI. If not provided, the
            most common ontology namespace in the file is auto-detected.

    Returns:
        A YAML string matching the format of
        ``GraphStore.schema_yaml(db_name)``.
    """
    g = Graph()
    g.parse(source)

    # Auto-detect ontology namespace if not provided
    if ontology_ns is None:
        ontology_ns = _detect_ontology_namespace(g)

    if ontology_ns is None:
        raise ValueError(
            "Could not auto-detect ontology namespace. "
            "Provide ontology_ns explicitly."
        )

    ONTO = Namespace(ontology_ns)

    # ── Discover all classes ───────────────────────────────────────
    # Only classes defined in the ontology namespace
    classes: Dict[str, Dict[str, Any]] = {}

    for s, p, o in g.triples((None, RDF.type, OWL_CLASS)):
        label = _local_name(str(s))
        if _in_namespace(str(s), ONTO):
            classes[label] = {
                "properties": {},
                "subClassOf": None,
                "hasKey": [],
                "uniqueIdentity": [],
                "datatype_props": {},
                "comment": "",
                "label": label,
            }

    for s, p, o in g.triples((None, RDF.type, RDFS_CLASS)):
        label = _local_name(str(s))
        if _in_namespace(str(s), ONTO):
            classes[label] = {
                "properties": {},
                "subClassOf": None,
                "hasKey": [],
                "uniqueIdentity": [],
                "datatype_props": {},
                "comment": "",
                "label": label,
            }

    # ── subClassOf hierarchy ───────────────────────────────────────
    for s, p, o in g.triples((None, RDFS.subClassOf, None)):
        s_str = str(s)
        o_str = str(o)
        if _in_namespace(s_str, ONTO) and _in_namespace(o_str, ONTO):
            child = _local_name(s_str)
            parent = _local_name(o_str)
            if child in classes:
                classes[child]["subClassOf"] = parent

    # ── owl:hasKey (primary keys) ─────────────────────────────────
    for s, p, o in g.triples((None, OWL_HAS_KEY, None)):
        cls_label = _local_name(str(s))
        if cls_label not in classes:
            continue
        prop_name = _local_name(str(o))
        classes[cls_label]["hasKey"].append(prop_name)

    # ── owl:uniqueIdentity ─────────────────────────────────────────
    for s, p, o in g.triples((None, OWL_UNIQUE_IDENTITY, None)):
        cls_label = _local_name(str(s))
        if cls_label not in classes:
            continue
        prop_name = _local_name(str(o))
        classes[cls_label]["uniqueIdentity"].append(prop_name)

    # ── rdfs:label and rdfs:comment ────────────────────────────────
    for s, p, o in g.triples((None, RDFS.label, None)):
        label_str = str(s)
        if _in_namespace(label_str, ONTO):
            cls_label = _local_name(label_str)
            if cls_label in classes:
                classes[cls_label]["label"] = str(o)

    for s, p, o in g.triples((None, RDFS.comment, None)):
        label_str = str(s)
        if _in_namespace(label_str, ONTO):
            cls_label = _local_name(label_str)
            if cls_label in classes:
                classes[cls_label]["comment"] = str(o)

    # ── DatatypeProperties → node properties ───────────────────────
    for s, p, o in g.triples((None, RDF.type, OWL.DatatypeProperty)):
        prop_name = _local_name(str(s))
        prop_type = _infer_datatype_property_type(g, s)

        for domain_uri in g.objects(s, RDFS.domain):
            d_str = str(domain_uri)
            if _in_namespace(d_str, ONTO):
                label = _local_name(d_str)
                if label in classes:
                    classes[label]["datatype_props"][prop_name] = prop_type

    # ── ObjectProperties → relationships ───────────────────────────
    relationships: Dict[str, Dict[str, Any]] = {}
    for s, p, o in g.triples((None, RDF.type, OWL.ObjectProperty)):
        rel_name = _local_name(str(s))
        domains = set()
        ranges = set()

        for d_uri in g.objects(s, RDFS.domain):
            d_str = str(d_uri)
            if _in_namespace(d_str, ONTO):
                domains.add(_local_name(d_str))
        for r_uri in g.objects(s, RDFS.range):
            r_str = str(r_uri)
            if _in_namespace(r_str, ONTO):
                ranges.add(_local_name(r_str))

        # Only keep relationships between ontology classes
        if domains or ranges:
            relationships[rel_name] = {
                "src": sorted(domains),
                "dst": sorted(ranges),
                "properties": {},
            }

    # ── Build YAML ─────────────────────────────────────────────────
    if yaml is None:
        raise ImportError(
            "PyYAML is required for schema generation. "
            "Install it with: pip install pyyaml"
        )

    data: Dict[str, Any] = {"labels": {}, "relationships": {}, "weak_relations": {}}

    # Labels
    for label in sorted(classes):
        info = classes[label]
        base_class = "WeakNode" if info["subClassOf"] is not None else "Node"

        entry: Dict[str, Any] = {
            "class_name": label,
            "base_class": base_class,
            "properties": info["datatype_props"],
            "primary_key": info["hasKey"] if info["hasKey"] else [],
            "count": 0,
        }

        if info["subClassOf"] is not None:
            entry["parent"] = info["subClassOf"]
            entry["parent_relation"] = _rel_name_from_hierarchy(label)

        if info["uniqueIdentity"]:
            entry["unique"] = info["uniqueIdentity"]

        # Add docstring from rdfs:comment
        if info["comment"]:
            entry["doc"] = info["comment"]

        data["labels"][label] = entry

    # Relationships
    for rel_name in sorted(relationships):
        info = relationships[rel_name]
        data["relationships"][rel_name] = {
            "class_name": _rel_to_class_name(rel_name),
            "src": ", ".join(info["src"]) if info["src"] else "Node",
            "dst": ", ".join(info["dst"]) if info["dst"] else "Node",
            "properties": info["properties"],
            "count": 0,
        }

    # WeakRelations (from subClassOf)
    for label in sorted(classes):
        info = classes[label]
        if info["subClassOf"] is not None:
            rel_name = _rel_name_from_hierarchy(label)
            data["weak_relations"][rel_name] = {
                "class_name": _rel_to_class_name(rel_name),
                "base_class": "WeakRelation",
                "propagate": True,
                "src": info["subClassOf"],
                "dst": label,
            }

    return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_ontology_namespace(g: Graph) -> Optional[str]:
    """Auto-detect the most common ontology namespace.

    Scans all URIs in the graph and returns the most common namespace
    prefix (excluding standard namespaces like rdf, rdfs, owl, xsd, skos).

    Args:
        g: RDF graph.

    Returns:
        The detected namespace URI, or None.
    """
    ns_counts: Dict[str, int] = {}
    EXCLUDED = {
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "http://www.w3.org/2000/01/rdf-schema#",
        "http://www.w3.org/2002/07/owl#",
        "http://www.w3.org/2001/XMLSchema#",
        "http://www.w3.org/2004/02/skos/core#",
        "http://purl.org/dc/elements/1.1/",
        "http://purl.org/dc/terms/",
        "http://purl.org/vocommons/voaf#",
    }

    for s, p, o in g:
        for term in (s, o):
            if isinstance(term, URIRef):
                uri = str(term)
                for ns in EXCLUDED:
                    if uri.startswith(ns):
                        break
                else:
                    # Find the namespace
                    for sep in ("#", "/"):
                        if sep in uri:
                            ns = uri[:uri.rindex(sep) + 1]
                            ns_counts[ns] = ns_counts.get(ns, 0) + 1
                    else:
                        ns_counts[uri] = ns_counts.get(uri, 0) + 1

    if not ns_counts:
        return None

    # Return the most common namespace
    return max(ns_counts, key=ns_counts.get)


def _in_namespace(uri: str, ns: Namespace) -> bool:
    """Check if a URI belongs to a namespace.

    Args:
        uri: The URI to check.
        ns: The namespace to test against.

    Returns:
        True if the URI starts with the namespace.
    """
    return str(uri).startswith(str(ns))


def _local_name(uri: str) -> str:
    """Extract a human-readable local name from a URI.

    Handles ``http://example.org/foo``, ``ex:foo``, ``<foo>``, etc.

    Args:
        uri: A URI string.

    Returns:
        The local name (e.g. ``"foo"``).
    """
    if "#" in uri:
        return uri.rsplit("#", 1)[-1]
    if "/" in uri:
        return uri.rsplit("/", 1)[-1]
    if ":" in uri:
        return uri.rsplit(":", 1)[-1]
    return uri


def _infer_datatype_property_type(g: Graph, prop_uri: URIRef) -> str:
    """Infer a Python/YAML type from a DatatypeProperty's rdfs:range.

    Args:
        g: RDF graph.
        prop_uri: The property URI.

    Returns:
        A type string (``"string"``, ``"integer"``, ``"float"``, etc.).
    """
    XSD = Namespace("http://www.w3.org/2001/XMLSchema#")
    XSD_INT = str(XSD.integer)
    XSD_FLOAT = str(XSD.float)
    XSD_DECIMAL = str(XSD.decimal)
    XSD_BOOLEAN = str(XSD.boolean)
    XSD_STRING = str(XSD.string)
    XSD_DATE = str(XSD.date)
    XSD_DATETIME = str(XSD.dateTime)

    # Check explicit rdfs:range
    for range_uri in g.objects(prop_uri, RDFS.range):
        range_str = str(range_uri)

        # Check xsd range
        if range_str.startswith(str(XSD)):
            local = range_str.split("#")[-1]
            if local in ("int", "integer", "long", "short", "byte"):
                return "integer"
            if local in ("float", "double", "decimal"):
                return "float"
            if local in ("boolean", "bool"):
                return "boolean"
            if local in ("date", "dateTime", "time"):
                return "string"
            if local in ("string", "anyURI", "token"):
                return "string"

    # Default: check actual literal values in the graph
    for s, p, o in g.triples((None, prop_uri, None)):
        if isinstance(o, Literal):
            dt = str(o.datatype) if o.datatype else ""
            if "integer" in dt or "int" in dt:
                return "integer"
            if "float" in dt or "double" in dt or "decimal" in dt:
                return "float"
            if "boolean" in dt:
                return "boolean"
            if "date" in dt or "time" in dt:
                return "string"
            return "string"

    return "string"


def _rel_name_from_hierarchy(child_label: str) -> str:
    """Generate a relation name from a WeakNode child label.

    Args:
        child_label: The child class label.

    Returns:
        A relation type name (e.g. ``"HAS_SECTION"``).
    """
    return "HAS_" + "_".join(child_label.upper().split())


def _rel_to_class_name(rel_type: str) -> str:
    """Convert a relation type to a Python class name.

    Args:
        rel_type: A relation type (e.g. ``"HAS_SECTION"``).

    Returns:
        A class name (e.g. ``"HasSection"``).
    """
    return "".join(w.capitalize() for w in rel_type.lower().split("_"))


# ---------------------------------------------------------------------------
# Schema generation — unified with schema_gen
# ---------------------------------------------------------------------------

from .schema_gen import generate_classes
