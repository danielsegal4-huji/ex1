"""
HW1 — Bookdelivery.com crawler. Top-level orchestrator.

Wires the three layers together:
    crawler.iter_book_links()  ->  parser.parse_book(html, category)  ->  processing.run_all(records)

Owner: shared (whichever member finishes first should glue this together).
Run with (from the project root):
    python -m src.books_crawler
"""

from __future__ import annotations

from datetime import datetime

from . import crawler
from . import parser as book_parser  # avoid shadowing stdlib `parser`
from . import processing


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> None:
    """End-to-end pipeline.

    Steps:
      1. Iterate over every book URL discovered by the crawler.
      2. Fetch the page HTML (polite delay handled inside crawler.get).
      3. Parse it into a flat dict of book fields.
      4. Hand the full list of records to the processing layer, which builds
         the DataFrame and writes every required CSV/JSON/PDF/ZIP artifact.
    """
    records: list[dict] = []
    try:
        for n, item in enumerate(crawler.iter_book_links(), 1):
            _log(f"[orchestrator] Book #{n} — fetching: {item['book_url']}")
            html = crawler.get(item["book_url"])
            _log(f"[orchestrator] Book #{n} — parsing …")
            record = book_parser.parse_book(html, item["source_category"])
            record["book_url"] = item["book_url"]
            records.append(record)
            _log(f"[orchestrator] Book #{n} done — title: {record.get('Title', '<missing>')!r}")
        _log(f"[orchestrator] All {len(records)} books fetched. Running processing …")
        processing.run_all(records)
        _log("[orchestrator] Done. All output files written.")
    finally:
        crawler.close_driver()


if __name__ == "__main__":
    main()
