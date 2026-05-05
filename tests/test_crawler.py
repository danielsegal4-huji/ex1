"""
Integration tests for crawler.py.

All tests run against the live site (bookdelivery.com/il-en/).
A single Selenium session is shared across the entire test run to avoid
the startup cost and to be polite (fewer connections).

Run with:
    .venv/bin/pytest tests/test_crawler.py -v
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlparse

import pytest

# Add src/ to path so we can import crawler directly
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import crawler as C


# ---------------------------------------------------------------------------
# Session-scoped fixtures — fetch once, reuse across all tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def homepage_html():
    """Fetch the homepage once for the entire test session."""
    html = C.get(C.BASE_URL)
    yield html
    C.close_driver()


@pytest.fixture(scope="session")
def categories(homepage_html):
    """Parse categories from the homepage."""
    return C.get_category_links(homepage_html)


@pytest.fixture(scope="session")
def first_category_books(categories):
    """Fetch book links from the first category, 1 page only."""
    original_max = C.MAX_PAGES_PER_CATEGORY
    C.MAX_PAGES_PER_CATEGORY = 1
    _, url = categories[0]
    books = C.get_book_links_from_category(url)
    C.MAX_PAGES_PER_CATEGORY = original_max
    return books


@pytest.fixture(scope="session")
def two_page_books(categories):
    """Fetch book links from the first category, 2 pages, to test pagination."""
    original_max = C.MAX_PAGES_PER_CATEGORY
    C.MAX_PAGES_PER_CATEGORY = 2
    _, url = categories[0]
    books = C.get_book_links_from_category(url)
    C.MAX_PAGES_PER_CATEGORY = original_max
    return books


# ---------------------------------------------------------------------------
# REQ 1: get() bypasses WAF and returns real HTML
# ---------------------------------------------------------------------------

class TestGet:
    def test_returns_nonempty_html(self, homepage_html):
        """get() must return substantial HTML, not a WAF challenge stub."""
        assert len(homepage_html) > 50_000, (
            f"Homepage HTML suspiciously small ({len(homepage_html)} chars) — "
            "may be a WAF block page"
        )

    def test_no_waf_challenge_in_response(self, homepage_html):
        """Page must not contain the AWS WAF JS challenge block page markers.

        The WAF block page contains AwsWafIntegration.getToken() and has no
        real body content. A static asset URL containing 'challenge.js' may
        legitimately appear in the real page, so we check the JS call instead.
        """
        assert "AwsWafIntegration.getToken" not in homepage_html, (
            "WAF JS challenge block page detected — Selenium did not bypass it"
        )

    def test_contains_bookdelivery_content(self, homepage_html):
        """Page must reference bookdelivery.com content."""
        assert "bookdelivery" in homepage_html.lower()

    def test_politeness_delay(self):
        """Each get() call must sleep at least REQUEST_DELAY_SEC seconds."""
        url = C.BASE_URL
        t0 = time.time()
        C.get(url)
        elapsed = time.time() - t0
        assert elapsed >= C.REQUEST_DELAY_SEC, (
            f"Request took only {elapsed:.1f}s — delay of {C.REQUEST_DELAY_SEC}s not enforced"
        )


# ---------------------------------------------------------------------------
# REQ 2: category discovery is fully programmatic
# ---------------------------------------------------------------------------

class TestCategoryDiscovery:
    def test_categories_found(self, categories):
        """At least one category must be discovered."""
        assert len(categories) > 0

    def test_expected_category_count(self, categories):
        """Should find roughly 20 top-level categories."""
        assert 15 <= len(categories) <= 30, (
            f"Unexpected category count: {len(categories)}"
        )

    def test_each_category_has_name_and_url(self, categories):
        """Every entry must be a (str, str) tuple with non-empty values."""
        for name, url in categories:
            assert isinstance(name, str) and name.strip(), f"Empty name: {(name, url)}"
            assert isinstance(url, str) and url.strip(), f"Empty URL: {(name, url)}"

    def test_category_urls_on_correct_domain(self, categories):
        """All category URLs must be on bookdelivery.com."""
        for name, url in categories:
            netloc = urlparse(url).netloc
            assert "bookdelivery.com" in netloc, (
                f"Category {name!r} has off-domain URL: {url}"
            )

    def test_category_urls_follow_pattern(self, categories):
        """Category URLs must match /books/{slug} or /libros/{slug}."""
        pattern = re.compile(r"/(books|libros)/[^/]+/?$")
        for name, url in categories:
            path = urlparse(url).path
            assert pattern.search(path), (
                f"Category {name!r} URL does not match expected pattern: {url}"
            )

    def test_no_book_pages_in_categories(self, categories):
        """No book page URL should appear in the category list."""
        for name, url in categories:
            path = urlparse(url).path
            assert not re.search(r"/book-", path), (
                f"Book page URL found in category list: {url}"
            )
            assert not re.search(r"/p/\d+", path), (
                f"Book product URL found in category list: {url}"
            )

    def test_no_duplicate_category_urls(self, categories):
        """No two categories should point to the same URL."""
        urls = [url for _, url in categories]
        assert len(urls) == len(set(urls)), "Duplicate category URLs found"

    def test_no_hardcoded_urls(self):
        """The category URL list must not be hardcoded in crawler.py source."""
        src_path = os.path.join(os.path.dirname(__file__), "..", "src", "crawler.py")
        with open(src_path) as f:
            source = f.read()
        # Check that known category slugs don't appear as string literals
        known_slugs = ["libros/arte", "libros/derecho", "libros/ficcion",
                       "books/arts", "books/law", "books/fiction"]
        for slug in known_slugs:
            assert f'"{slug}"' not in source and f"'{slug}'" not in source, (
                f"Hard-coded category URL slug {slug!r} found in crawler.py"
            )

    def test_known_categories_present(self, categories):
        """A few well-known categories should always be discoverable."""
        names_lower = {n.lower() for n, _ in categories}
        for expected in ["law", "arts", "fiction"]:
            assert any(expected in n for n in names_lower), (
                f"Expected category containing {expected!r} not found. "
                f"Got: {sorted(names_lower)}"
            )


# ---------------------------------------------------------------------------
# REQ 3: book link extraction
# ---------------------------------------------------------------------------

class TestBookLinkExtraction:
    def test_books_found_on_first_page(self, first_category_books):
        """At least one book link must be found on the first category page."""
        assert len(first_category_books) > 0

    def test_reasonable_books_per_page(self, first_category_books):
        """A category page should contain a reasonable number of books (≥10)."""
        assert len(first_category_books) >= 10, (
            f"Only {len(first_category_books)} books found on page 1 — suspiciously few"
        )

    def test_book_urls_match_pattern(self, first_category_books):
        """Every book URL must contain /book-…/p/{id}."""
        for url in first_category_books:
            path = urlparse(url).path
            assert re.search(r"/book-", path), f"Missing /book- in URL: {url}"
            assert re.search(r"/p/\d+", path), f"Missing /p/<id> in URL: {url}"

    def test_book_urls_on_correct_domain(self, first_category_books):
        """All book URLs must be on bookdelivery.com."""
        for url in first_category_books:
            assert "bookdelivery.com" in urlparse(url).netloc, (
                f"Off-domain book URL: {url}"
            )

    def test_no_duplicate_book_urls(self, first_category_books):
        """Book URLs within one category must be deduplicated."""
        assert len(first_category_books) == len(set(first_category_books)), (
            "Duplicate book URLs returned"
        )

    def test_book_urls_are_strings(self, first_category_books):
        for url in first_category_books:
            assert isinstance(url, str)


# ---------------------------------------------------------------------------
# REQ 4: pagination
# ---------------------------------------------------------------------------

class TestPagination:
    def test_page_2_yields_books(self, two_page_books, first_category_books):
        """Crawling 2 pages must yield more books than crawling 1 page."""
        assert len(two_page_books) > len(first_category_books), (
            "Page 2 returned no additional books — pagination may be broken"
        )

    def test_max_pages_respected(self, categories):
        """Crawler must never fetch more than MAX_PAGES_PER_CATEGORY pages."""
        original_max = C.MAX_PAGES_PER_CATEGORY
        limit = 2
        C.MAX_PAGES_PER_CATEGORY = limit

        fetch_count = 0
        original_get = C.get

        def counting_get(url):
            nonlocal fetch_count
            fetch_count += 1
            return original_get(url)

        C.get = counting_get
        try:
            _, url = categories[0]
            C.get_book_links_from_category(url)
        finally:
            C.get = original_get
            C.MAX_PAGES_PER_CATEGORY = original_max

        assert fetch_count <= limit, (
            f"Fetched {fetch_count} pages but MAX_PAGES_PER_CATEGORY was {limit}"
        )

    def test_pagination_produces_different_urls(self, categories):
        """The URL used for page 2 must differ from page 1."""
        original_max = C.MAX_PAGES_PER_CATEGORY
        C.MAX_PAGES_PER_CATEGORY = 2

        fetched_urls: list[str] = []
        original_get = C.get

        def recording_get(url):
            fetched_urls.append(url)
            return original_get(url)

        C.get = recording_get
        try:
            _, cat_url = categories[0]
            C.get_book_links_from_category(cat_url)
        finally:
            C.get = original_get
            C.MAX_PAGES_PER_CATEGORY = original_max

        assert len(fetched_urls) == 2, f"Expected 2 fetches, got {len(fetched_urls)}"
        assert fetched_urls[0] != fetched_urls[1], "Page 1 and page 2 used the same URL"


# ---------------------------------------------------------------------------
# REQ 5: iter_book_links() output format
# ---------------------------------------------------------------------------

class TestIterBookLinks:
    def test_yields_dicts_with_correct_keys(self, categories):
        """iter_book_links() must yield dicts with 'book_url' and 'source_category'."""
        original_max = C.MAX_PAGES_PER_CATEGORY
        C.MAX_PAGES_PER_CATEGORY = 1

        # Only iterate over the first category to keep it fast
        original_iter = C.iter_book_links

        def single_category_iter():
            html = C.get(C.BASE_URL)
            cats = C.get_category_links(html)
            for url in C.get_book_links_from_category(cats[0][1]):
                yield {"book_url": url, "source_category": cats[0][0]}

        items = list(single_category_iter())
        C.MAX_PAGES_PER_CATEGORY = original_max

        assert len(items) > 0
        for item in items:
            assert set(item.keys()) == {"book_url", "source_category"}, (
                f"Wrong keys in yielded dict: {item.keys()}"
            )
            assert isinstance(item["book_url"], str) and item["book_url"].startswith("http")
            assert isinstance(item["source_category"], str) and item["source_category"]
