from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import books_crawler


class ResumePersistenceTest(unittest.TestCase):
    """Tests for the JSONL-based resume mechanism in books_crawler."""

    def setUp(self):
        # Each test gets its own temp directory and a fresh PARTIAL_PATH
        # pointing inside it. The original module-level path is restored in
        # tearDown so other tests / real runs aren't affected.
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_path = books_crawler.PARTIAL_PATH
        books_crawler.PARTIAL_PATH = Path(self._tmpdir.name) / "partial.jsonl"

    def tearDown(self):
        books_crawler.PARTIAL_PATH = self._original_path
        self._tmpdir.cleanup()

    def test_load_when_file_missing_returns_empty(self):
        records, seen = books_crawler._load_partial()
        self.assertEqual(records, [])
        self.assertEqual(seen, set())

    def test_append_then_load_single_record(self):
        record = {
            "Title": "Genghis Khan",
            "Authors": "Weatherford, Jack",
            "PriceNIS": 52.35,
            "book_url": "https://example.com/book/1",
        }
        books_crawler._append_partial(record)

        records, seen = books_crawler._load_partial()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0], record)
        self.assertEqual(seen, {"https://example.com/book/1"})

    def test_append_multiple_then_load_preserves_order(self):
        records_in = [
            {"Title": "Book A", "book_url": "https://example.com/a"},
            {"Title": "Book B", "book_url": "https://example.com/b"},
            {"Title": "Book C", "book_url": "https://example.com/c"},
        ]
        for record in records_in:
            books_crawler._append_partial(record)

        records_out, seen = books_crawler._load_partial()
        self.assertEqual(records_out, records_in)
        self.assertEqual(
            seen,
            {"https://example.com/a", "https://example.com/b", "https://example.com/c"},
        )

    def test_load_skips_malformed_lines(self):
        # Hand-write a file with one good line, one corrupted line (simulating
        # a half-written record at the moment of crash), and another good line.
        good_a = {"Title": "A", "book_url": "https://example.com/a"}
        good_b = {"Title": "B", "book_url": "https://example.com/b"}
        with books_crawler.PARTIAL_PATH.open("w", encoding="utf-8") as f:
            f.write(json.dumps(good_a) + "\n")
            f.write('{"Title": "B", "book_url": "https://exam')  # truncated, no newline
            f.write("\n")
            f.write(json.dumps(good_b) + "\n")
            f.write("\n")  # blank line - also skipped

        records, seen = books_crawler._load_partial()
        self.assertEqual(records, [good_a, good_b])
        self.assertEqual(seen, {"https://example.com/a", "https://example.com/b"})

    def test_record_without_book_url_loaded_but_not_in_seen(self):
        # Defensive: if a record somehow lacks book_url, it should still load
        # but contribute nothing to the seen set.
        record = {"Title": "Orphan record", "PriceNIS": 10.0}
        books_crawler._append_partial(record)

        records, seen = books_crawler._load_partial()
        self.assertEqual(records, [record])
        self.assertEqual(seen, set())

    def test_unicode_round_trip(self):
        # Spanish accents and Hebrew characters should survive a write+read.
        record = {
            "Title": "El Reino de Hierro: Auge y Caída de Prusia",
            "Authors": "Christopher Clark",
            "PriceNIS": 118.45,
            "book_url": "https://example.com/הספר",
        }
        books_crawler._append_partial(record)

        records, seen = books_crawler._load_partial()
        self.assertEqual(records, [record])
        self.assertIn("https://example.com/הספר", seen)

    def test_append_creates_parent_directory(self):
        # If output/ doesn't exist yet, _append_partial should create it.
        nested = Path(self._tmpdir.name) / "deeper" / "partial.jsonl"
        books_crawler.PARTIAL_PATH = nested
        self.assertFalse(nested.parent.exists())

        books_crawler._append_partial({"Title": "X", "book_url": "https://x"})
        self.assertTrue(nested.exists())


if __name__ == "__main__":
    unittest.main()
