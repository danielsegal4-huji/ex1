"""
HW1 — Bookdelivery.com crawler. Top-level orchestrator.

Wires the three layers together:
    crawler.iter_book_links()  ->  parser.parse_book(html, category)  ->  processing.run_all(records)

Run with (from the project root):
    python -m src.books_crawler

Resume support: every parsed book is appended to output/books_partial.jsonl
immediately. If the run crashes, just re-run the same command — already-fetched
URLs are loaded from that file and skipped. To start fresh, delete the file.
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


def _load_partial() -> tuple[list[dict], set[str]]:
    """Read previously-saved records (if any). Returns (records, seen_urls)."""
    records: list[dict] = []
    seen: set[str] = set()
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
            if url:
                seen.add(url)
    return records, seen


def _append_partial(record: dict) -> None:
    PARTIAL_PATH.parent.mkdir(exist_ok=True)
    with PARTIAL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    """End-to-end pipeline.

    Steps:
      1. Load any previously-saved records from output/books_partial.jsonl.
      2. Iterate over every book URL discovered by the crawler.
         Skip URLs already present in the resume file.
      3. Fetch the page HTML, parse it, and append the record to the resume file.
      4. After the crawl finishes, hand the full list of records to processing,
         which builds the DataFrame and writes every required artifact.
    """
    records, seen = _load_partial()
    if records:
        _log(f"[orchestrator] Resuming: loaded {len(records)} previously-saved records.")

    try:
        for n, item in enumerate(crawler.iter_book_links(), 1):
            url = item["book_url"]
            if url in seen:
                _log(f"[orchestrator] Book #{n} - skipping (already done): {url}")
                continue

            _log(f"[orchestrator] Book #{n} - fetching: {url}")
            html = crawler.get(url)
            _log(f"[orchestrator] Book #{n} - parsing ...")
            record = book_parser.parse_book(html, item["source_category"])
            record["book_url"] = url
            records.append(record)
            seen.add(url)
            _append_partial(record)
            _log(f"[orchestrator] Book #{n} done - title: {record.get('Title', '<missing>')!r}")

        _log(f"[orchestrator] All {len(records)} books fetched. Running processing ...")
        processing.run_all(records)
        _log("[orchestrator] Done. All output files written.")
    finally:
        crawler.close_driver()


if __name__ == "__main__":
    main()
