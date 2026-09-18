"""Load RiC-O data from the National Archives of France into Neo4j.

Downloads RDF/XML files from the National Archives of France RiC-O examples
and imports them into Neo4j using Cypher MERGE commands.

The loader:

1. Downloads RDF files from GitHub (agents, recordResources, relations, vocabularies)
2. Parses them with ``rdflib``
3. Extracts nodes and relationships
4. Uses Cypher MERGE to insert nodes and relationships into Neo4j
5. Returns load statistics

Usage::

    from cvcdocdb.exemples.load_ric_o_naf import load_ric_o_naf

    stats = load_ric_o_naf(graph, limit=50, max_agents=10, max_records=3)
    print(f"Total entities: {stats['total']}")
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional, Set, Tuple

from cvcdocdb.neo4j_graph import _validate_cypher_identifier

try:
    import rdflib
    from rdflib import RDF, RDFS, OWL, Graph, URIRef, Literal, Namespace
except ImportError:  # pragma: no cover
    raise ImportError(
        "rdflib is required for RiC-O loading. "
        "Install it with: pip install rdflib"
    )

# ---------------------------------------------------------------------------
# URLs for the National Archives of France RiC-O examples
# ---------------------------------------------------------------------------

REPO_OWNER = "ICA-EGAD"
REPO_NAME = "RiC-O"
BRANCH = "master"
VERSION = "examples_v0-1"  # v1-1 restructured; v0-1 has the original NAF files
NAF_BASE = f"examples/{VERSION}/NationalArchivesOfFrance/rdf-xml"

# GitHub API base (for listing directory contents)
API_BASE = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents"

# Raw content base (for downloading files)
RAW_BASE = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}"

# Category subdirectories within the NAF rdf-xml directory
AGENT_URL = f"{RAW_BASE}/{NAF_BASE}/agents"
RECORD_URL = f"{RAW_BASE}/{NAF_BASE}/recordResources"
RELATION_URL = f"{RAW_BASE}/{NAF_BASE}/relations"
VOCAB_URL = f"{RAW_BASE}/{NAF_BASE}/vocabularies"

# Namespace for the RiC-O ontology
ONTO_NS = "https://www.ica.org/standards/RiC/ontology#"
ONTO = Namespace(ONTO_NS)

# ---------------------------------------------------------------------------
# RDF → DRM label mapping
# ---------------------------------------------------------------------------

RDF_TO_LABEL: Dict[str, str] = {
    str(ONTO.Record): "Record",
    str(ONTO.Agent): "Agent",
    str(ONTO.CorporateBody): "CorporateBody",
    str(ONTO.Individual): "Person",
    str(ONTO.AgentName): "AgentName",
    str(ONTO.RecordResource): "RecordResource",
    str(ONTO.RecordSet): "RecordSet",
    str(ONTO.Instantiation): "Instantiation",
    str(ONTO.Activity): "Activity",
    str(ONTO.ActivityType): "ActivityType",
    str(ONTO.DocumentaryFormType): "DocumentaryFormType",
    str(ONTO.Language): "Language",
    str(ONTO.CorporateBodyType): "CorporateBodyType",
    str(ONTO.Place): "Place",
    str(ONTO.PlaceName): "PlaceName",
    str(ONTO.PhysicalLocation): "PhysicalLocation",
    str(ONTO.Identifier): "Identifier",
    str(ONTO.Date): "Date",
    str(ONTO.Title): "Title",
    str(ONTO.Appellation): "Appellation",
    str(ONTO.Mechanism): "Mechanism",
    str(ONTO.RoleType): "RoleType",
    str(ONTO.Rule): "Rule",
    str(ONTO.Position): "Position",
    str(ONTO.Group): "Group",
    str(ONTO.Family): "Family",
    str(ONTO.FamilyType): "FamilyType",
    str(ONTO.Event): "Event",
    str(ONTO.EventType): "EventType",
    str(ONTO.RecordState): "RecordState",
    str(ONTO.Extent): "Extent",
    str(ONTO.ExtentType): "ExtentType",
    str(ONTO.UnitOfMeasurement): "UnitOfMeasurement",
    str(ONTO.ContentType): "ContentType",
    str(ONTO.DemographicGroup): "DemographicGroup",
    str(ONTO.LegalStatus): "LegalStatus",
    str(ONTO.Mandate): "Mandate",
    str(ONTO.MandateType): "MandateType",
    str(ONTO.OccupationType): "OccupationType",
    str(ONTO.Proxy): "Proxy",
    str(ONTO.RecordPart): "RecordPart",
    str(ONTO.Relation): "Relation",
    str(ONTO.Type): "Type",
}

# ---------------------------------------------------------------------------
# Helper: download a directory listing from GitHub
# ---------------------------------------------------------------------------


def _http_get(url: str, headers: Optional[Dict[str, str]] = None,
              retries: int = 3, backoff: float = 2.0) -> bytes:
    """HTTP GET with exponential backoff retry.

    Args:
        url: The URL to fetch.
        headers: Optional HTTP headers.
        retries: Number of retries on transient errors.
        backoff: Initial backoff in seconds.

    Returns:
        Response body as bytes.
    """
    if headers is None:
        headers = {}
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:  # Rate limited
                wait = backoff * (2 ** attempt)
                print(f"    Rate limited ({e.code}), retrying in {wait:.1f}s...")
                time.sleep(wait)
            elif e.code in (502, 503, 504):  # Transient server errors
                wait = backoff * (2 ** attempt)
                print(f"    Server error ({e.code}), retrying in {wait:.1f}s...")
                time.sleep(wait)
            else:
                raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                wait = backoff * (2 ** attempt)
                print(f"    Connection error, retrying in {wait:.1f}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"Failed to fetch {url} after {retries} retries")


def _list_github_dir(url: str) -> List[str]:
    """List files in a GitHub raw directory URL.

    Uses the GitHub API to list directory contents since raw URLs
    do not support directory listing.

    Args:
        url: The GitHub raw URL of the directory.

    Returns:
        List of filenames.
    """
    # Convert raw URL to API path:
    # https://raw.githubusercontent.com/ICA-EGAD/RiC-O/master/examples_v0-1/.../agents
    # -> examples_v0-1/.../agents
    path = url
    for prefix in (
        f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}/",
        f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/",
    ):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break

    api_url = f"{API_BASE}/{path}"
    headers = {
        "User-Agent": f"cvcdocdb-rico/1.0",
        "Accept": "application/vnd.github.v3+json",
    }

    data_bytes = _http_get(api_url, headers)
    data = json.loads(data_bytes.decode("utf-8"))

    if isinstance(data, dict) and "message" in data:
        # Directory doesn't exist or is empty — try listing parent and filtering
        return []

    return [
        item["name"]
        for item in data
        if isinstance(item, dict) and item.get("type") == "file" and item["name"].endswith(".rdf")
    ]


def _download_file(url: str) -> bytes:
    """Download a file from a URL.

    Args:
        url: The URL to download.

    Returns:
        The file content as bytes.
    """
    return _http_get(url, headers={"User-Agent": "cvcdocdb-rico/1.0"})


# ---------------------------------------------------------------------------
# RDF parsing
# ---------------------------------------------------------------------------


def _parse_rdf(data: bytes) -> Graph:
    """Parse RDF/XML bytes into an rdflib Graph.

    Args:
        data: RDF/XML content as bytes.

    Returns:
        Parsed rdflib Graph.
    """
    g = Graph()
    g.parse(data=data, format="xml")
    return g


def _extract_node_id(uri: URIRef, base_uri: str) -> str:
    """Extract a stable node ID from a URI.

    Converts ``https://rdf.archives-nationales.culture.gouv.fr/agent/000005``
    to ``agent/000005``.

    Args:
        uri: The RDF URI.
        base_uri: The xml:base of the document.

    Returns:
        A stable node ID string.
    """
    uri_str = str(uri)
    if base_uri and uri_str.startswith(base_uri):
        return uri_str[len(base_uri):]
    # Fallback: extract last path segment
    for sep in ("#", "/"):
        if sep in uri_str:
            return uri_str.rsplit(sep, 1)[-1]
    return str(uri)


def _local_name(uri: str) -> str:
    """Extract a local name from a URI."""
    if "#" in uri:
        return uri.rsplit("#", 1)[-1]
    if "/" in uri:
        return uri.rsplit("/", 1)[-1]
    if ":" in uri:
        return uri.rsplit(":", 1)[-1]
    return uri


def _parse_rdf_file(
    url: str,
    category: str = "",
) -> Tuple[Graph, str]:
    """Parse a single RDF file and return the graph and its base URI.

    Args:
        url: The URL of the RDF file.
        category: Category name for logging.

    Returns:
        Tuple of (parsed graph, base URI).
    """
    data = _download_file(url)
    g = _parse_rdf(data)

    # Try to find xml:base from the graph
    base_uri = ""
    for s, p, o in g:
        if str(p) == "http://www.w3.org/XML/1998/namespace#base":
            base_uri = str(o)
            break

    return g, base_uri


# ---------------------------------------------------------------------------
# Node extraction
# ---------------------------------------------------------------------------


def _extract_nodes(
    g: Graph,
    base_uri: str,
    category: str,
) -> List[Dict[str, Any]]:
    """Extract nodes from an RDF graph.

    Args:
        g: The parsed RDF graph.
        base_uri: The base URI of the document.
        category: Category name (agents, recordResources, etc.).

    Returns:
        List of node dicts with keys: id, labels, properties.
    """
    nodes: Dict[str, Dict[str, Any]] = {}

    # First pass: find all subjects with known RDF types
    for s, p, o in g:
        if not isinstance(s, URIRef):
            continue
        if not isinstance(p, URIRef) or str(p) != str(RDF.type):
            continue
        if not isinstance(o, URIRef):
            continue

        obj_str = str(o)
        label = RDF_TO_LABEL.get(obj_str)
        if label is None:
            continue

        node_id = _extract_node_id(s, base_uri)
        if node_id in nodes:
            if label not in nodes[node_id]["labels"]:
                nodes[node_id]["labels"].append(label)
            continue

        nodes[node_id] = {
            "id": node_id,
            "labels": [label],
            "properties": {},
        }

    # Second pass: extract properties for each node
    for node_id in list(nodes.keys()):
        uri = URIRef(base_uri + node_id) if base_uri else URIRef(node_id)

        for prop_uri, prop in g.predicate_objects(uri):
            prop_str = str(prop_uri)
            prop_name = _local_name(prop_str)

            # Skip RDF type and subClassOf
            if prop_str == str(RDF.type):
                continue
            if prop_str == str(RDFS.subClassOf):
                continue

            if isinstance(prop, Literal):
                val = str(prop)
                if prop.language:
                    val = f"{val}@{prop.language}"
                # Avoid overwriting with empty values
                if prop_name not in nodes[node_id]["properties"] or not nodes[node_id]["properties"][prop_name]:
                    nodes[node_id]["properties"][prop_name] = val
            elif isinstance(prop, URIRef):
                ref_id = _extract_node_id(prop, base_uri)
                prop_key = f"_ref_{_local_name(prop_str)}"
                if prop_key not in nodes[node_id]["properties"]:
                    nodes[node_id]["properties"][prop_key] = []
                if ref_id not in nodes[node_id]["properties"][prop_key]:
                    nodes[node_id]["properties"][prop_key].append(ref_id)

    return list(nodes.values())


# ---------------------------------------------------------------------------
# Relationship extraction
# ---------------------------------------------------------------------------

# Map of RiC-O properties → (rel_type, src_label, dst_label)
# Used to generate Cypher MERGE for relationships
# Both old (hasOrHad/isOrWas) and new (has/is) property names are supported.
RELATION_MAP: Dict[str, Tuple[str, str, str]] = {
    # ── Organic provenance ──────────────────────────────────────────
    str(ONTO.hasOrganicProvenance): ("HAS_ORGANIC_PROVENANCE", "RecordResource", "Agent"),
    str(ONTO.isProvenanceOf): ("IS_ORGANIC_PROVENANCE_OF", "Agent", "RecordResource"),
    str(ONTO.isOrWasOrganicProvenanceOf): ("IS_ORGANIC_PROVENANCE_OF", "Agent", "RecordResource"),
    # ── Instantiation ───────────────────────────────────────────────
    str(ONTO.hasInstantiation): ("HAS_INSTANTIATION", "RecordResource", "Instantiation"),
    str(ONTO.hasOrHadInstantiation): ("HAS_INSTANTIATION", "RecordResource", "Instantiation"),
    str(ONTO.instantiates): ("IS_INSTANTIATION_OF", "Instantiation", "RecordResource"),
    str(ONTO.isOrWasInstantiationOf): ("IS_INSTANTIATION_OF", "Instantiation", "RecordResource"),
    # ── Holder / held by ────────────────────────────────────────────
    str(ONTO.heldBy): ("HAS_HOLDER", "Instantiation", "Agent"),
    str(ONTO.hasOrHadHolder): ("HAS_HOLDER", "Instantiation", "Agent"),
    # ── Location ────────────────────────────────────────────────────
    str(ONTO.hasOrHadLocation): ("HAS_LOCATION", "Instantiation", "PhysicalLocation"),
    # ── Agent names ─────────────────────────────────────────────────
    str(ONTO.hasAgentName): ("HAS_AGENT_NAME", "Agent", "AgentName"),
    str(ONTO.hasOrHadAgentName): ("HAS_AGENT_NAME", "Agent", "AgentName"),
    str(ONTO.isAgentNameOf): ("IS_AGENT_NAME_OF", "AgentName", "Agent"),
    str(ONTO.isOrWasAgentNameOf): ("IS_AGENT_NAME_OF", "AgentName", "Agent"),
    # ── Creator / created by ────────────────────────────────────────
    str(ONTO.createdBy): ("HAS_CREATOR", "Thing", "Agent"),
    str(ONTO.hasOrHadCreator): ("HAS_CREATOR", "Thing", "Agent"),
    str(ONTO.hasCreator): ("HAS_CREATOR", "Thing", "Agent"),
    str(ONTO.hasPublisher): ("HAS_PUBLISHER", "Thing", "Agent"),
    # ── Description relations ───────────────────────────────────────
    str(ONTO.describedBy): ("IS_DESCRIBED_BY", "Agent", "Thing"),
    str(ONTO.isOrWasDescribedBy): ("IS_DESCRIBED_BY", "Agent", "Thing"),
    str(ONTO.describes): ("DESCRIBES_OR_DESCRIBED", "Thing", "Agent"),
    str(ONTO.describesOrDescribed): ("DESCRIBES_OR_DESCRIBED", "Thing", "Agent"),
    # ── Agent-to-Agent relations ────────────────────────────────────
    str(ONTO.hasOrHadController): ("HAS_CONTROLLER", "Agent", "Agent"),
    str(ONTO.hasOrHadEmployer): ("HAS_EMPLOYER", "Agent", "Agent"),
    str(ONTO.hasOrHadMember): ("HAS_MEMBER", "Group", "Agent"),
    str(ONTO.hasOrHadSpouse): ("HAS_SPOUSE", "Agent", "Agent"),
    str(ONTO.hasOrHadStudent): ("HAS_STUDENT", "Agent", "Agent"),
    str(ONTO.hasOrHadTeacher): ("HAS_TEACHER", "Agent", "Agent"),
    str(ONTO.hasOrHadChild): ("HAS_CHILD", "Agent", "Agent"),
    str(ONTO.hasOrHadParent): ("HAS_PARENT", "Agent", "Agent"),
    # ── Hierarchical relations ──────────────────────────────────────
    str(ONTO.agentIsSourceOfAgentHierarchicalRelation): (
        "HAS_HIERARCHICAL_RELATION", "Agent", "Agent"
    ),
    str(ONTO.hasOrHadHierarchicalRelation): ("HAS_HIERARCHICAL_RELATION", "Agent", "Agent"),
    # ── Temporal relations ──────────────────────────────────────────
    str(ONTO.hasOrHadTemporalRelation): ("HAS_TEMPORAL_RELATION", "Agent", "Agent"),
    # ── Agent-to-agent relation proxy ───────────────────────────────
    str(ONTO.agentIsConnectedToAgentRelation): ("HAS_AGENT_TO_AGENT_RELATION", "Agent", "Agent"),
    str(ONTO.hasOrHadAgentToAgentRelation): ("HAS_AGENT_TO_AGENT_RELATION", "Agent", "Agent"),
    # ── Subject / mainsubject ───────────────────────────────────────
    str(ONTO.hasOrHadSubject): ("HAS_SUBJECT", "RecordResource", "Thing"),
    str(ONTO.hasOrHadMainsubject): ("HAS_MAIN_SUBJECT", "RecordResource", "Thing"),
    # ── Language ────────────────────────────────────────────────────
    str(ONTO.hasLanguage): ("HAS_LANGUAGE", "Thing", "Language"),
    str(ONTO.hasOrHadLanguage): ("HAS_LANGUAGE", "Thing", "Language"),
    # ── Documentary form type ───────────────────────────────────────
    str(ONTO.hasDocumentaryFormType): ("HAS_DOCUMENTARY_FORM_TYPE", "Thing", "DocumentaryFormType"),
    str(ONTO.hasOrHadDocumentaryFormType): ("HAS_DOCUMENTARY_FORM_TYPE", "Thing", "DocumentaryFormType"),
    # ── Corporate body type ─────────────────────────────────────────
    str(ONTO.hasOrHadCorporateBodyType): ("HAS_CORPORATE_BODY_TYPE", "CorporateBody", "CorporateBodyType"),
    # ── Identifier ──────────────────────────────────────────────────
    str(ONTO.hasOrHadIdentifier): ("HAS_IDENTIFIER", "Agent", "Identifier"),
    # ── Birth / death dates ─────────────────────────────────────────
    str(ONTO.hasOrHadBirthDate): ("HAS_BIRTH_DATE", "Agent", "Date"),
    str(ONTO.hasOrHadDeathDate): ("HAS_DEATH_DATE", "Agent", "Date"),
    str(ONTO.hasOrHadBirthPlace): ("HAS_BIRTH_PLACE", "Agent", "PhysicalLocation"),
    str(ONTO.hasOrHadDeathPlace): ("HAS_DEATH_PLACE", "Agent", "PhysicalLocation"),
    # ── Occupation ──────────────────────────────────────────────────
    str(ONTO.hasOrHadOccupation): ("HAS_OCCUPATION", "Agent", "OccupationType"),
    # ── Position / role ─────────────────────────────────────────────
    str(ONTO.hasOrHadPosition): ("HAS_POSITION", "Agent", "Position"),
    str(ONTO.hasOrHadRole): ("HAS_ROLE", "Agent", "RoleType"),
    # ── RecordResource properties ───────────────────────────────────
    str(ONTO.hasOrHadDate): ("HAS_DATE", "RecordResource", "Date"),
    str(ONTO.hasOrHadTitle): ("HAS_TITLE", "RecordResource", "Title"),
    str(ONTO.hasOrHadExtent): ("HAS_EXTENT", "RecordResource", "Extent"),
    str(ONTO.hasOrHadContentType): ("HAS_CONTENT_TYPE", "RecordResource", "ContentType"),
    str(ONTO.hasOrHadRecordState): ("HAS_RECORD_STATE", "RecordResource", "RecordState"),
    str(ONTO.hasLegalStatus): ("HAS_LEGAL_STATUS", "RecordResource", "LegalStatus"),
    str(ONTO.hasOrHadLegalStatus): ("HAS_LEGAL_STATUS", "RecordResource", "LegalStatus"),
    str(ONTO.hasOrHadCreationDate): ("HAS_CREATION_DATE", "RecordResource", "Date"),
    str(ONTO.hasOrHadPublicationDate): ("HAS_PUBLICATION_DATE", "RecordResource", "Date"),
    str(ONTO.hasOrHadModificationDate): ("HAS_MODIFICATION_DATE", "RecordResource", "Date"),
    str(ONTO.hasOrHadMigrationDate): ("HAS_MIGRATION_DATE", "RecordResource", "Date"),
    # ── Instantiation-to-instantiation relations ────────────────────
    str(ONTO.hasOrHadDerivationRelation): ("HAS_DERIVATION_RELATION", "Instantiation", "Instantiation"),
    str(ONTO.hasOrHadAnalogueInstantiation): ("HAS_ANALOGUE_INSTANTIATION", "Instantiation", "Instantiation"),
    str(ONTO.hasOrHadDigitalInstantiation): ("HAS_DIGITAL_INSTANTIATION", "Thing", "Instantiation"),
    # ── RecordResource-to-RecordResource relations ──────────────────
    str(ONTO.hasOrHadDirectPart): ("HAS_DIRECT_PART", "RecordResource", "RecordResource"),
    str(ONTO.isOrWasPartOf): ("IS_PART_OF", "RecordResource", "RecordResource"),
    str(ONTO.directlyPrecedesInSequence): ("DIRECTLY_PRECEDES", "RecordResource", "RecordResource"),
    # ── Activity relations ──────────────────────────────────────────
    str(ONTO.isOrWasAffectedBy): ("IS_AFFECTED_BY", "Thing", "Activity"),
    # ── Activity type ───────────────────────────────────────────────
    str(ONTO.hasActivityType): ("HAS_ACTIVITY_TYPE", "Activity", "ActivityType"),
    # ── Performance relations ───────────────────────────────────────
    str(ONTO.agentIsTargetOfPerformanceRelation): ("HAS_PERFORMANCE_TARGET", "PerformanceRelation", "Agent"),
    str(ONTO.agentIsSourceOfPerformanceRelation): ("HAS_PERFORMANCE_SOURCE", "Activity", "PerformanceRelation"),
    str(ONTO.performanceRelationHasTarget): ("HAS_PERFORMANCE_TARGET", "PerformanceRelation", "Agent"),
    # ── Group subdivision ───────────────────────────────────────────
    str(ONTO.groupIsSourceOfGroupSubdivisionRelation): (
        "HAS_GROUP_SUBDIVISION", "Group", "Group"
    ),
    # ── Agent origination relation ──────────────────────────────────
    str(ONTO.agentIsTargetOfAgentOriginationRelation): (
        "HAS_AGENT_ORIGINATION", "AgentOriginationRelation", "Agent"
    ),
    # ── Rules ───────────────────────────────────────────────────────
    str(ONTO.regulatedBy): ("HAS_REGULATING_RULE", "Thing", "Rule"),
}


def _extract_relationships(
    g: Graph,
    base_uri: str,
    category: str,
) -> List[Dict[str, Any]]:
    """Extract relationships from an RDF graph.

    Args:
        g: The parsed RDF graph.
        base_uri: The base URI of the document.
        category: Category name.

    Returns:
        List of relationship dicts with keys: src_id, dst_id, type, src_label, dst_label.
    """
    rels: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str]] = set()

    for s, p, o in g:
        if not isinstance(s, URIRef) or not isinstance(o, URIRef):
            continue

        prop_str = str(p)
        mapping = RELATION_MAP.get(prop_str)
        if mapping is None:
            continue

        rel_type, src_label, dst_label = mapping

        src_id = _extract_node_id(s, base_uri)
        dst_id = _extract_node_id(o, base_uri)

        # Deduplicate
        key = (src_id, dst_id, rel_type)
        if key in seen:
            continue
        seen.add(key)

        rels.append({
            "src_id": src_id,
            "dst_id": dst_id,
            "type": rel_type,
            "src_label": src_label,
            "dst_label": dst_label,
        })

    return rels


# ---------------------------------------------------------------------------
# Neo4j import via Cypher MERGE
# ---------------------------------------------------------------------------


def _import_nodes_via_cypher(
    graph: Any,
    nodes: List[Dict[str, Any]],
) -> int:
    """Import nodes into Neo4j using Cypher MERGE commands.

    For each node, generates a MERGE query that:
    - Matches or creates the node by its unique ID
    - Sets all properties

    Args:
        graph: Neo4jGraph instance.
        nodes: List of node dicts.

    Returns:
        Number of nodes imported.
    """
    if not nodes:
        return 0

    count = 0
    for node in nodes:
        node_id = node["id"]
        # Labels come from the fixed RDF_TO_LABEL mapping, not from RDF
        # content directly, but validate anyway before splicing them as
        # bare Cypher identifiers (labels/relationship types cannot be
        # parameterized in Cypher).
        for lbl in node["labels"]:
            _validate_cypher_identifier(lbl, "label")
        labels = ":".join(node["labels"])

        # node_id and property names/values originate from the downloaded
        # RDF/XML content and are not trusted. Pass them as query
        # parameters instead of splicing them into the query text.
        props: Dict[str, Any] = dict(node["properties"])

        query = f"MERGE (n:{labels} {{id: $node_id}})"
        if props:
            query += " SET n += $props"

        try:
            graph.query(query, params={"node_id": node_id, "props": props})
            count += 1
        except Exception:
            # Skip nodes that fail (e.g., constraint violations)
            pass

    return count


def _import_rels_via_cypher(
    graph: Any,
    rels: List[Dict[str, Any]],
) -> int:
    """Import relationships into Neo4j using Cypher MATCH + CREATE commands.

    For each relationship:
    - MATCHes the source and target nodes by ID
    - Creates the relationship with the specified type

    Args:
        graph: Neo4jGraph instance.
        rels: List of relationship dicts.

    Returns:
        Number of relationships imported.
    """
    if not rels:
        return 0

    count = 0
    for rel in rels:
        src_id = rel["src_id"]
        dst_id = rel["dst_id"]
        rel_type = rel["type"]
        src_label = rel.get("src_label", "Node")
        dst_label = rel.get("dst_label", "Node")

        # rel_type/labels come from the fixed RELATION_MAP mapping, but
        # validate before splicing as bare Cypher identifiers. src_id/dst_id
        # come from RDF content and are passed as query parameters instead.
        for identifier in (rel_type, src_label, dst_label):
            _validate_cypher_identifier(identifier, "label/relation type")

        query = (
            f"MATCH (a:{src_label} {{id: $src_id}}) "
            f"MATCH (b:{dst_label} {{id: $dst_id}}) "
            f"MERGE (a)-[r:{rel_type}]->(b)"
        )

        try:
            graph.query(query, params={"src_id": src_id, "dst_id": dst_id})
            count += 1
        except Exception:
            # Skip relationships that fail (e.g., nodes not yet found)
            pass

    return count


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------


def load_ric_o_naf(
    graph: Any,
    limit: int = 50,
    max_agents: int = 10,
    max_records: int = 3,
) -> Dict[str, int]:
    """Load RiC-O data from the National Archives of France into Neo4j.

    Downloads RDF/XML files from the National Archives of France RiC-O
    examples and imports them into Neo4j using Cypher MERGE commands.

    Args:
        graph: Neo4jGraph instance.
        limit: Total entities to load (soft limit).
        max_agents: Number of agent files to load.
        max_records: Number of record resource files to load.

    Returns:
        Dict with load statistics:
            - agents: number of agent nodes
            - records: number of record resource nodes
            - things: number of thing nodes
            - children: number of child entity nodes
            - relationships: number of relationships
            - total: total entities loaded
    """
    all_nodes: Dict[str, Dict[str, Any]] = {}
    all_rels: List[Dict[str, Any]] = []
    counters: Dict[str, int] = {
        "agents": 0,
        "records": 0,
        "things": 0,
        "children": 0,
        "relationships": 0,
        "total": 0,
    }

    try:
        # ── 1. Download and parse agent files ─────────────────────────
        agent_files = _list_github_dir(AGENT_URL)[:max_agents]
        print(f"  Downloading {len(agent_files)} agent files...")
        for fname in agent_files:
            url = f"{AGENT_URL}/{fname}"
            g, base_uri = _parse_rdf_file(url, category="agents")

            nodes = _extract_nodes(g, base_uri, "agents")
            rels = _extract_relationships(g, base_uri, "agents")

            for node in nodes:
                if node["id"] not in all_nodes:
                    all_nodes[node["id"]] = node
                    if "Agent" in node["labels"] or "CorporateBody" in node["labels"] or "Person" in node["labels"]:
                        counters["agents"] += 1
                    elif "Thing" in node["labels"]:
                        counters["things"] += 1
                    else:
                        counters["children"] += 1

            all_rels.extend(rels)

        # ── 2. Download and parse record resource files ───────────────
        record_files = _list_github_dir(RECORD_URL)[:max_records]
        print(f"  Downloading {len(record_files)} record resource files...")
        for fname in record_files:
            url = f"{RECORD_URL}/{fname}"
            g, base_uri = _parse_rdf_file(url, category="recordResources")

            nodes = _extract_nodes(g, base_uri, "recordResources")
            rels = _extract_relationships(g, base_uri, "recordResources")

            for node in nodes:
                if node["id"] not in all_nodes:
                    all_nodes[node["id"]] = node
                    if "RecordResource" in node["labels"] or "RecordSet" in node["labels"]:
                        counters["records"] += 1
                    elif "Thing" in node["labels"]:
                        counters["things"] += 1
                    elif "Instantiation" in node["labels"]:
                        counters["children"] += 1
                    else:
                        counters["children"] += 1

            all_rels.extend(rels)

        # ── 3. Download and parse relation files ──────────────────────
        relation_files = _list_github_dir(RELATION_URL)
        print(f"  Downloading {len(relation_files)} relation files...")
        for fname in relation_files[:5]:  # Limit relation files
            url = f"{RELATION_URL}/{fname}"
            g, base_uri = _parse_rdf_file(url, category="relations")

            nodes = _extract_nodes(g, base_uri, "relations")
            rels = _extract_relationships(g, base_uri, "relations")

            for node in nodes:
                if node["id"] not in all_nodes:
                    all_nodes[node["id"]] = node
                    counters["children"] += 1

            all_rels.extend(rels)

        # ── 4. Import nodes into Neo4j ────────────────────────────────
        print(f"  Importing {len(all_nodes)} nodes...")
        node_count = _import_nodes_via_cypher(graph, list(all_nodes.values()))
        print(f"  Imported {node_count} nodes.")

        # ── 5. Import relationships into Neo4j ────────────────────────
        print(f"  Importing {len(all_rels)} relationships...")
        rel_count = _import_rels_via_cypher(graph, all_rels)
        print(f"  Imported {rel_count} relationships.")

        counters["relationships"] = rel_count
        counters["total"] = node_count

    except Exception as exc:
        print(f"  Error loading data: {exc}")
        raise

    return counters
