"""Game of Thrones-style loader for Neo4jGraph.

Downloads public character data and maps it into Character/House nodes.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Dict, List

from cvcdocdb.base import Node, Relation
from cvcdocdb.exemples._http import read_capped


THRONES_CHARACTERS_URL = "https://thronesapi.com/api/v2/Characters"

FALLBACK_CHARACTERS = [
    {"id": 1, "fullName": "Jon Snow", "title": "King in the North", "family": "Stark"},
    {"id": 2, "fullName": "Arya Stark", "title": "No One", "family": "Stark"},
    {"id": 3, "fullName": "Daenerys Targaryen", "title": "Mother of Dragons", "family": "Targaryen"},
]


def _download_characters(limit: int = 60) -> List[dict]:
    req = urllib.request.Request(THRONES_CHARACTERS_URL, headers={"User-Agent": "cvcdocdb-examples/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(read_capped(resp, url=THRONES_CHARACTERS_URL).decode("utf-8"))
        if isinstance(data, list):
            return data[:limit]
    except Exception:
        pass
    return FALLBACK_CHARACTERS[: min(len(FALLBACK_CHARACTERS), limit)]


def load_got_characters(graph, limit: int = 60) -> Dict[str, int]:
    """Load a Game of Thrones-like graph into a Neo4jGraph instance.

    Nodes:
        - Character(id, name, title)
        - House(name)

    Relations:
        - (Character)-[:MEMBER_OF]->(House)

    Args:
        graph: Neo4jGraph instance.
        limit: Max characters to ingest.

    Returns:
        Dict with inserted counters.
    """
    chars = _download_characters(limit=limit)
    counters = {"characters": 0, "houses": 0, "member_of": 0}
    seen_houses = set()

    for item in chars:
        cid = item.get("id")
        full_name = item.get("fullName") or "Unknown"
        if cid is None:
            continue

        house_name = item.get("family") or "Unknown"

        char_node = Node(
            pk={"character_id": int(cid)},
            main_label="Character",
            name=full_name,
            title=item.get("title"),
            source="thronesapi",
        )
        graph.insertNode(char_node, replace=True)
        counters["characters"] += 1

        house_node = Node(pk={"house_name": house_name}, main_label="House", name=house_name)
        if house_name not in seen_houses:
            graph.insertNode(house_node, replace=True)
            seen_houses.add(house_name)
            counters["houses"] += 1

        graph.insertRelation(Relation(char_node, house_node, "MEMBER_OF"), update=True)
        counters["member_of"] += 1

    return counters

