"""
tests/unit/test_writer.py
Unit tests for database deduplication and hash utilities.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestComputeBytesHash:
    def test_deterministic(self):
        from database.writer import compute_bytes_hash
        data = b"invoice content"
        assert compute_bytes_hash(data) == compute_bytes_hash(data)

    def test_different_content_different_hash(self):
        from database.writer import compute_bytes_hash
        assert compute_bytes_hash(b"invoice A") != compute_bytes_hash(b"invoice B")

    def test_returns_64_char_hex(self):
        from database.writer import compute_bytes_hash
        h = compute_bytes_hash(b"test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestParseDate:
    def test_valid_iso_date(self):
        from database.writer import _parse_date
        from datetime import date
        assert _parse_date("2024-03-15") == date(2024, 3, 15)

    def test_none_returns_none(self):
        from database.writer import _parse_date
        assert _parse_date(None) is None

    def test_invalid_string_returns_none(self):
        from database.writer import _parse_date
        assert _parse_date("not-a-date") is None

    def test_date_object_passthrough(self):
        from database.writer import _parse_date
        from datetime import date
        d = date(2024, 6, 1)
        assert _parse_date(d) == d
