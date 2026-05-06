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
            "Category": "Biography",
            "Authors": "Weatherford, Jack",
            "PriceNIS": 52.35,
            "book_url": "https://example.com/book/1",
        }
        books_crawler._append_partial(record)

        records, seen = books_crawler._load_partial()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0], record)
        self.assertEqual(seen, {("https://example.com/book/1", "Biography")})

    def test_append_multiple_then_load_preserves_order(self):
        records_in = [
            {"Title": "Book A", "Category": "Art", "book_url": "https://example.com/a"},
            {"Title": "Book B", "Category": "Law", "book_url": "https://example.com/b"},
            {"Title": "Book C", "Category": "History", "book_url": "https://example.com/c"},
        ]
        for record in records_in:
            books_crawler._append_partial(record)

        records_out, seen = books_crawler._load_partial()
        self.assertEqual(records_out, records_in)
        self.assertEqual(
            seen,
            {
                ("https://example.com/a", "Art"),
                ("https://example.com/b", "Law"),
                ("https://example.com/c", "History"),
            },
        )

    def test_same_url_under_different_categories_both_in_seen(self):
        # The whole point of the (url, category) dedup: a book that appears
        # under two categories must produce two distinct seen entries so a
        # resumed run still parses it for both categories.
        record_bio = {
            "Title": "Napoleon",
            "Category": "Biography",
            "book_url": "https://example.com/napoleon",
        }
        record_mil = {
            "Title": "Napoleon",
            "Category": "Military History",
            "book_url": "https://example.com/napoleon",
        }
        books_crawler._append_partial(record_bio)
        books_crawler._append_partial(record_mil)

        records, seen = books_crawler._load_partial()
        self.assertEqual(records, [record_bio, record_mil])
        self.assertEqual(
            seen,
            {
                ("https://example.com/napoleon", "Biography"),
                ("https://example.com/napoleon", "Military History"),
            },
        )

    def test_load_skips_malformed_lines(self):
        # Hand-write a file with one good line, one corrupted line (simulating
        # a half-written record at the moment of crash), and another good line.
        good_a = {"Title": "A", "Category": "Art", "book_url": "https://example.com/a"}
        good_b = {"Title": "B", "Category": "Law", "book_url": "https://example.com/b"}
        with books_crawler.PARTIAL_PATH.open("w", encoding="utf-8") as f:
            f.write(json.dumps(good_a) + "\n")
            f.write('{"Title": "B", "book_url": "https://exam')  # truncated, no newline
            f.write("\n")
            f.write(json.dumps(good_b) + "\n")
            f.write("\n")  # blank line - also skipped

        records, seen = books_crawler._load_partial()
        self.assertEqual(records, [good_a, good_b])
        self.assertEqual(
            seen,
            {("https://example.com/a", "Art"), ("https://example.com/b", "Law")},
        )

    def test_record_missing_url_or_category_not_in_seen(self):
        # Defensive: if a record lacks book_url OR Category, it should still
        # load but contribute nothing to the seen set.
        no_url = {"Title": "No URL", "Category": "Art"}
        no_cat = {"Title": "No category", "book_url": "https://example.com/x"}
        books_crawler._append_partial(no_url)
        books_crawler._append_partial(no_cat)

        records, seen = books_crawler._load_partial()
        self.assertEqual(records, [no_url, no_cat])
        self.assertEqual(seen, set())

    def test_unicode_round_trip(self):
        # Spanish accents and Hebrew characters should survive a write+read.
        record = {
            "Title": "El Reino de Hierro: Auge y Caída de Prusia",
            "Category": "Historia",
            "Authors": "Christopher Clark",
            "PriceNIS": 118.45,
            "book_url": "https://example.com/הספר",
        }
        books_crawler._append_partial(record)

        records, seen = books_crawler._load_partial()
        self.assertEqual(records, [record])
        self.assertIn(("https://example.com/הספר", "Historia"), seen)

    def test_append_creates_parent_directory(self):
        # If output/ doesn't exist yet, _append_partial should create it.
        nested = Path(self._tmpdir.name) / "deeper" / "partial.jsonl"
        books_crawler.PARTIAL_PATH = nested
        self.assertFalse(nested.parent.exists())

        books_crawler._append_partial({"Title": "X", "Category": "Y", "book_url": "https://x"})
        self.assertTrue(nested.exists())


if __name__ == "__main__":
    unittest.main()
