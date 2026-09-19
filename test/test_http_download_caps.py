"""Tests for the download size caps that guard against a malicious/misbehaving
server forcing unbounded memory use (DoS via oversized HTTP response)."""

from __future__ import annotations

import io
import unittest
from unittest import mock

from cvcdocdb.exemples._http import read_capped


class _FakeResponse:
    """Mimics the chunked .read(n) interface of an open urllib response."""

    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


class ReadCappedTest(unittest.TestCase):
    def test_reads_body_under_the_cap(self) -> None:
        resp = _FakeResponse(b"hello world")
        self.assertEqual(read_capped(resp, max_bytes=1024), b"hello world")

    def test_raises_when_body_exceeds_the_cap(self) -> None:
        resp = _FakeResponse(b"x" * 200)
        with self.assertRaises(ValueError):
            read_capped(resp, max_bytes=100)

    def test_error_message_includes_url(self) -> None:
        resp = _FakeResponse(b"x" * 200)
        with self.assertRaises(ValueError) as ctx:
            read_capped(resp, max_bytes=100, url="https://example.com/huge.rdf")
        self.assertIn("https://example.com/huge.rdf", str(ctx.exception))


class DownloadOntologyCapTest(unittest.TestCase):
    def test_download_ontology_rejects_oversized_response(self) -> None:
        from cvcdocdb.rdf_schema import download_ontology, MAX_ONTOLOGY_BYTES

        fake_resp = _FakeResponse(b"x" * (MAX_ONTOLOGY_BYTES + 1))

        class _CM:
            def __enter__(self):
                return fake_resp

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=_CM()):
            with self.assertRaises(ValueError):
                download_ontology("https://example.com/huge.rdf", output_dir="/tmp/does-not-matter")


if __name__ == "__main__":
    unittest.main()
