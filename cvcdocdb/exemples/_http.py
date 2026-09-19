"""Shared HTTP helper for the example dataset loaders.

Bounds how much data a single HTTP response can contribute in memory,
so a compromised or misbehaving server (GitHub, OpenAlex, ...) cannot
force these example scripts to exhaust memory by returning an
unexpectedly huge response body.
"""

from __future__ import annotations

from typing import Any

#: Default cap for JSON API responses (GitHub API listings, OpenAlex pages).
DEFAULT_MAX_RESPONSE_BYTES = 20 * 1024 * 1024  # 20 MB

_CHUNK_SIZE = 65536


def read_capped(resp: Any, max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES, url: str = "") -> bytes:
    """Read an HTTP response body, aborting if it exceeds ``max_bytes``.

    Args:
        resp: An open response object (as returned by ``urllib.request.urlopen``).
        max_bytes: Maximum number of bytes to accept.
        url: The request URL, used only for the error message.

    Returns:
        The full response body.

    Raises:
        ValueError: If the response body exceeds ``max_bytes``.
    """
    chunks = []
    total = 0
    while True:
        chunk = resp.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(
                f"Response from {url or 'remote server'} exceeded the "
                f"{max_bytes} byte limit; aborting download."
            )
        chunks.append(chunk)
    return b"".join(chunks)
