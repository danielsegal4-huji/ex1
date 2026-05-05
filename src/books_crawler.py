"""
HW1 — Bookdelivery.com crawler. Top-level orchestrator.

Wires the three layers together:
    crawler.iter_book_links()  ->  parser.parse_book(html, category)  ->  processing.run_all(records)

Owner: shared (whichever member finishes first should glue this together).
Run with (from the project root):
    python -m src.books_crawler
"""

from __future__ import annotations

from . import crawler
from . import parser as book_parser  # avoid shadowing stdlib `parser`
from . import processing


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
        for item in crawler.iter_book_links():
            html = crawler.get(item["book_url"])
            record = book_parser.parse_book(html, item["source_category"])
            records.append(record)
        processing.run_all(records)
    finally:
        crawler.close_driver()


if __name__ == "__main__":
    main()
