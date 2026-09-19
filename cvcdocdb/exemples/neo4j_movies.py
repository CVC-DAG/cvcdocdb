"""Movie-domain loader for Neo4jGraph.

Downloads a public movies list and maps it into a Neo4j graph with DRM.
The structure mirrors the classic Neo4j Movies style (Person-Movie domain)
using available fields from the source endpoint.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Dict, List

from cvcdocdb.base import Node, Relation
from cvcdocdb.exemples._http import read_capped


SAMPLE_MOVIES_URL = "https://api.sampleapis.com/movies/animation"

FALLBACK_MOVIES = [
    {"id": 1, "title": "The Matrix", "imdbId": "tt0133093", "genre": "Sci-Fi"},
    {"id": 2, "title": "The Godfather", "imdbId": "tt0068646", "genre": "Crime"},
    {"id": 3, "title": "Toy Story", "imdbId": "tt0114709", "genre": "Animation"},
]


def _download_movies(limit: int = 40) -> List[dict]:
    req = urllib.request.Request(SAMPLE_MOVIES_URL, headers={"User-Agent": "cvcdocdb-examples/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(read_capped(resp, url=SAMPLE_MOVIES_URL).decode("utf-8"))
        if isinstance(data, list):
            return data[:limit]
    except Exception:
        pass
    return FALLBACK_MOVIES[: min(len(FALLBACK_MOVIES), limit)]


def load_movies_sample(graph, limit: int = 40) -> Dict[str, int]:
    """Load a movie graph into a Neo4jGraph instance.

    Nodes:
        - Movie(id, title, imdbId)
        - Genre(name)

    Relations:
        - (Movie)-[:IN_GENRE]->(Genre)

    Args:
        graph: Neo4jGraph instance.
        limit: Max movies to ingest.

    Returns:
        Dict with inserted counters.
    """
    movies = _download_movies(limit=limit)
    counters = {"movies": 0, "genres": 0, "in_genre": 0}
    seen_genres = set()

    for item in movies:
        movie_id = item.get("id")
        title = item.get("title")
        if movie_id is None or not title:
            continue

        movie = Node(
            pk={"movie_id": int(movie_id)},
            main_label="Movie",
            title=title,
            imdb_id=item.get("imdbId"),
            source="sampleapis",
        )
        graph.insertNode(movie, replace=True)
        counters["movies"] += 1

        genre_name = item.get("genre") or "Animation"
        genre = Node(pk={"name": str(genre_name)}, main_label="Genre", name=str(genre_name))
        if genre_name not in seen_genres:
            graph.insertNode(genre, replace=True)
            seen_genres.add(genre_name)
            counters["genres"] += 1

        graph.insertRelation(Relation(movie, genre, "IN_GENRE"), update=True)
        counters["in_genre"] += 1

    return counters

