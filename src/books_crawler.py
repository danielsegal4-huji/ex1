"""
HW1 — Bookdelivery.com crawler. Top-level orchestrator.

Wires the three layers together:
    crawler.iter_book_links()  ->  parser.parse_book(html, category)  ->  processing.run_all(records)

Run with (from the project root):
    python -m src.books_crawler

Resume support: every parsed book is appended to output/books_partial.jsonl
immediately. If the run crashes, just re-run the same command — already-fetched
(URL, source_category) pairs are loaded from that file and skipped.
To start fresh, delete the file.

Why (URL, category) and not just URL: the same book can appear under several
top-level categories on the site. We want one row per (URL, category) pair so
the dataset records *which navigation path* found the book each time. Skipping
purely on URL would silently drop those extra rows on a resumed run.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# Force UTF-8 stdout so log messages with non-ASCII chars (arrows, em-dashes,
# Spanish category names, Hebrew titles) don't crash on Windows' cp1252 console.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from . import crawler
from . import parser as book_parser  # avoid shadowing stdlib `parser`
from . import processing


PARTIAL_PATH = Path("output/books_partial.jsonl")


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _load_partial() -> tuple[list[dict], set[tuple[str, str]]]:
    """Read previously-saved records (if any).

    Returns (records, seen_pairs), where seen_pairs is a set of
    (book_url, category) tuples — the dedup key for skipping on resume.
    """
    records: list[dict] = []
    seen: set[tuple[str, str]] = set()
    if not PARTIAL_PATH.exists():
        return records, seen
    with PARTIAL_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # Skip malformed lines (e.g. partial write at the moment of crash).
                continue
            records.append(record)
            url = record.get("book_url")
            category = record.get("Category")
            if url and category:
                seen.add((url, category))
    return records, seen


def _append_partial(record: dict) -> None:
    PARTIAL_PATH.parent.mkdir(exist_ok=True)
    with PARTIAL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    """End-to-end pipeline.

    Steps:
      1. Load any previously-saved records from output/books_partial.jsonl into
         a cache keyed by (url, category).
      2. Iterate over every (book_url, source_category) pair the crawler yields.
         For each pair: if it's already in the cache, take that record; otherwise
         fetch+parse fresh and append to the resume file.
      3. The final records list is built in iteration order, so a resumed run
         produces the same row order as a clean run — including books that
         appear under multiple categories.
      4. Hand the full list to processing, which builds the DataFrame and
         writes every required artifact.

    Why (URL, category) is the right dedup key: a book that appears under
    multiple top-level categories yields one record per category. Skipping on
    URL alone would silently drop those extra rows.
    """
    cached_records, _ = _load_partial()
    cache: dict[tuple[str, str], dict] = {}
    for record in cached_records:
        url = record.get("book_url")
        category = record.get("Category")
        if url and category:
            cache[(url, category)] = record
    if cache:
        _log(f"[orchestrator] Resuming: loaded {len(cache)} previously-saved records.")

    records: list[dict] = []
    new_count = 0

    try:
        for n, item in enumerate(crawler.iter_book_links(), 1):
            url = item["book_url"]
            category = item["source_category"]
            pair = (url, category)

            if pair in cache:
                records.append(cache[pair])
                if n % 200 == 0:
                    _log(f"[orchestrator] Progress: {n} pairs seen, {new_count} newly fetched.")
                continue

            _log(f"[orchestrator] Book #{n} - fetching ({category!r}): {url}")
            html = crawler.get(url)
            record = book_parser.parse_book(html, category)
            record["book_url"] = url
            records.append(record)
            cache[pair] = record
            _append_partial(record)
            new_count += 1
            _log(f"[orchestrator] Book #{n} done - title: {record.get('Title', '<missing>')!r}")

        _log(f"[orchestrator] Done crawling. {len(records)} total records ({new_count} newly fetched). Running processing ...")
        processing.run_all(records)
        _log("[orchestrator] All output files written.")
    finally:
        crawler.close_driver()


if __name__ == "__main__":
    main()
