"""Bibliographic references loader for NetworkXGraph.

Downloads a small set of works from OpenAlex and builds a citation graph.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Dict, List

from cvcdocdb.base import Node, Relation
from cvcdocdb.exemples._http import read_capped


OPENALEX_BASE_URL = "https://api.openalex.org/works"

FALLBACK_WORKS = [
    {
        "id": "https://openalex.org/W-FALLBACK-1",
        "display_name": "Graph Databases: Concepts",
        "publication_year": 2022,
        "authorships": [
            {"author": {"id": "https://openalex.org/A-FALLBACK-1", "display_name": "Ada Graph"}},
            {"author": {"id": "https://openalex.org/A-FALLBACK-2", "display_name": "Neo Query"}},
        ],
        "referenced_works": ["https://openalex.org/W-FALLBACK-2"],
    },
    {
        "id": "https://openalex.org/W-FALLBACK-2",
        "display_name": "Property Graph Modeling",
        "publication_year": 2021,
        "authorships": [
            {"author": {"id": "https://openalex.org/A-FALLBACK-2", "display_name": "Neo Query"}},
        ],
        "referenced_works": [],
    },
]


def _download_openalex_works(query: str, per_page: int = 20, mailto: str | None = None) -> List[dict]:
    params = {"search": query, "per-page": str(per_page)}
    if mailto:
        params["mailto"] = mailto
    url = f"{OPENALEX_BASE_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "cvcdocdb-examples/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(read_capped(resp, url=url).decode("utf-8"))
        return payload.get("results", [])
    except Exception:
        # Keep the tutorial runnable offline or in restrictive SSL environments.
        return FALLBACK_WORKS[: min(len(FALLBACK_WORKS), per_page)]


def load_bibliografia_openalex(
    graph,
    query: str = "graph database",
    per_page: int = 20,
    mailto: str | None = None,
) -> Dict[str, int]:
    """Download OpenAlex works and load them as a reference graph.

    Nodes:
        - Paper (openalex_id, title, year)
        - Author (author_id, display_name)

    Relations:
        - (Author)-[:AUTHORED]->(Paper)
        - (Paper)-[:CITES]->(Paper)  [only for works present in this batch]

    Args:
        graph: Graph backend instance (typically NetworkXGraph).
        query: OpenAlex search text.
        per_page: Max works to retrieve.
        mailto: Optional mailto parameter recommended by OpenAlex.

    Returns:
        Dict with inserted counters.
    """
    works = _download_openalex_works(query=query, per_page=per_page, mailto=mailto)

    counters = {"papers": 0, "authors": 0, "authored": 0, "cites": 0}
    seen_authors = set()

    # Insert papers first and index by OpenAlex id for citation edges.
    works_by_id = {}
    for w in works:
        wid = w.get("id")
        if not wid:
            continue
        works_by_id[wid] = w
        paper = Node(
            pk={"openalex_id": wid},
            main_label="Paper",
            title=w.get("display_name", ""),
            year=w.get("publication_year"),
            source="openalex",
        )
        graph.insertNode(paper, replace=True)
        counters["papers"] += 1

    for w in works_by_id.values():
        paper = Node(pk={"openalex_id": w["id"]}, main_label="Paper")

        for auth in w.get("authorships", []):
            author = auth.get("author", {})
            aid = author.get("id")
            aname = author.get("display_name", "")
            if not aid:
                continue

            author_node = Node(pk={"author_id": aid}, main_label="Author", name=aname, source="openalex")
            if aid not in seen_authors:
                graph.insertNode(author_node, replace=True)
                seen_authors.add(aid)
                counters["authors"] += 1

            graph.insertRelation(Relation(author_node, paper, "AUTHORED"), update=True)
            counters["authored"] += 1

        for cited_wid in w.get("referenced_works", []):
            if cited_wid not in works_by_id:
                continue
            cited = Node(pk={"openalex_id": cited_wid}, main_label="Paper")
            graph.insertRelation(Relation(paper, cited, "CITES"), update=True)
            counters["cites"] += 1

    return counters

