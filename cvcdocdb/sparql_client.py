"""Minimal SPARQL 1.1/1.2 protocol client (HTTP, standard library only).

Used by :class:`~cvcdocdb.jena_graph.JenaGraph` to talk to Apache Jena
Fuseki (or any SPARQL endpoint with separate query and update URLs).
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

#: Default seconds to wait for the server.
DEFAULT_TIMEOUT = 30.0
_MAX_ERROR_CHARS = 500


class SparqlError(RuntimeError):
    """The SPARQL server rejected a request or could not be reached."""


class SparqlClient:
    """POST queries/updates to a SPARQL endpoint.

    Args:
        query_url: SPARQL query endpoint (e.g. ``http://host:3030/ds/query``).
        update_url: SPARQL update endpoint (e.g. ``http://host:3030/ds/update``).
        user: Optional HTTP basic-auth user.
        password: Optional HTTP basic-auth password.
        timeout: Seconds to wait for each request.
    """

    def __init__(
        self,
        query_url: str,
        update_url: str,
        user: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.query_url = query_url
        self.update_url = update_url
        self.timeout = timeout
        self._headers: Dict[str, str] = {}
        if user:
            token = base64.b64encode(f"{user}:{password or ''}".encode()).decode()
            self._headers["Authorization"] = f"Basic {token}"

    def _post(self, url: str, field: str, text: str, accept: Optional[str] = None) -> bytes:
        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8", **self._headers}
        if accept:
            headers["Accept"] = accept
        data = urllib.parse.urlencode({field: text}).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - URL given by the caller
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:_MAX_ERROR_CHARS]
            raise SparqlError(f"SPARQL server returned HTTP {exc.code} for {url}: {body.strip()}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise SparqlError(f"Could not reach the SPARQL server at {url}: {exc}") from exc

    def select(self, query: str) -> List[Dict[str, Any]]:
        """Run a SELECT query; returns the JSON result bindings."""
        raw = self._post(self.query_url, "query", query, "application/sparql-results+json")
        return json.loads(raw)["results"]["bindings"]

    def ask(self, query: str) -> bool:
        raw = self._post(self.query_url, "query", query, "application/sparql-results+json")
        return bool(json.loads(raw)["boolean"])

    def graph(self, query: str) -> str:
        """Run a CONSTRUCT/DESCRIBE query; returns N-Triples text."""
        return self._post(self.query_url, "query", query, "application/n-triples").decode("utf-8")

    def update(self, update: str) -> None:
        """Run a SPARQL Update request (atomic on Fuseki)."""
        self._post(self.update_url, "update", update)
