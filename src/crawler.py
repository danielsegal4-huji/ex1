"""
crawler.py  —  Owner: Member A

Responsible for everything network-facing:
  * Selenium-based fetching (bypasses AWS WAF JS challenge)
  * discovering all top-level category links from the homepage
  * walking up to 5 pagination pages per category
  * extracting individual book-page URLs from each pagination page

The orchestrator (books_crawler.py) only ever calls:
  * get(url)               -> str  (HTML)
  * iter_book_links()      -> Iterator[{"book_url": str, "source_category": str}]

Spec rules enforced here:
  * NO hand-coded category or book URLs — everything discovered programmatically.
  * Significant delay between requests (REQUEST_DELAY_SEC).
  * requests library cannot pass AWS WAF JS challenge; Selenium with headless
    Chrome is used instead.
"""

from __future__ import annotations

import re
import time
from typing import Iterator
from urllib.parse import urljoin, urlparse, urlencode, urlunparse, parse_qs

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

BASE_URL = "https://www.bookdelivery.com/il-en/"
REQUEST_DELAY_SEC = 3
MAX_PAGES_PER_CATEGORY = 5

# ---------------------------------------------------------------------------
# Selenium driver (module-level singleton)
# ---------------------------------------------------------------------------

def _make_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    # Use system Chrome if available
    import shutil, os
    chrome_candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome") or "",
        shutil.which("chromium") or "",
    ]
    for path in chrome_candidates:
        if path and os.path.exists(path):
            opts.binary_location = path
            break

    driver = webdriver.Chrome(options=opts)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


_driver: webdriver.Chrome | None = None


def _get_driver() -> webdriver.Chrome:
    global _driver
    if _driver is None:
        print("[crawler] Starting headless Chrome …")
        _driver = _make_driver()
    return _driver


def close_driver() -> None:
    """Call this when the crawl is done to release the browser process."""
    global _driver
    if _driver is not None:
        _driver.quit()
        _driver = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get(url: str) -> str:
    """Fetch `url` with headless Chrome and return page HTML as text.

    Sleeps REQUEST_DELAY_SEC before each request (politeness).
    Waits up to 8 s for JS rendering after the page loads.
    Retries up to 2 times on transient failures (network errors, blank pages).
    """
    max_retries = 2
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        time.sleep(REQUEST_DELAY_SEC)
        try:
            driver = _get_driver()
            driver.get(url)
            time.sleep(8)
            html = driver.page_source
            if len(html) > 500:  # guard against blank/error pages
                return html
            last_exc = RuntimeError(f"Page too short ({len(html)} chars): {url}")
        except Exception as exc:
            last_exc = exc

        if attempt < max_retries:
            backoff = 2 ** (attempt + 1)
            print(f"[crawler] Transient error on {url!r}, retrying in {backoff}s …")
            time.sleep(backoff)

    raise RuntimeError(f"[crawler] Permanent failure fetching {url!r}") from last_exc


def get_category_links(homepage_html: str) -> list[tuple[str, str]]:
    """Parse the homepage HTML and return every top-level category link.

    Returns:
        A list of (category_name, category_absolute_url) tuples.

    Strategy:
        The site renders categories as <a href="/libros/{slug}"> links
        (global) AND as <a href="/il-en/books/{slug}"> links (localised).
        Both point to the same categories, so we deduplicate by normalised
        name and prefer the /il-en/ URL when both are present.
    """
    soup = BeautifulSoup(homepage_html, "lxml")

    # Collect all candidates: {normalised_name: (display_name, url)}
    # il-en URLs take priority over /libros/ ones.
    by_name: dict[str, tuple[str, str]] = {}

    for tag in soup.find_all("a", href=True):
        href: str = tag["href"].strip()
        absolute = urljoin(BASE_URL, href)

        if urlparse(absolute).netloc not in urlparse(BASE_URL).netloc:
            continue
        if not _is_category_url(absolute):
            continue

        name = _clean_text(tag.get_text())
        if not name:
            continue

        is_local = "/il-en/" in absolute
        key = name.lower()
        existing = by_name.get(key)
        if existing is None or (is_local and "/il-en/" not in existing[1]):
            by_name[key] = (name, absolute)

    # Prefer the /il-en/books/ set (our locale); if none found fall back to all
    local_results = [(n, u) for n, u in by_name.values() if "/il-en/" in u]
    results = local_results if local_results else list(by_name.values())

    if not results:
        raise RuntimeError(
            "[crawler] No category links found on the homepage. "
            "Inspect homepage HTML and update _is_category_url()."
        )

    return results


def get_book_links_from_category(category_url: str) -> list[str]:
    """Walk up to MAX_PAGES_PER_CATEGORY pagination pages and return all book URLs.

    Pagination on bookdelivery.com uses ?page=N query parameter.
    """
    book_urls: list[str] = []
    current_url: str | None = category_url

    for page_num in range(1, MAX_PAGES_PER_CATEGORY + 1):
        if current_url is None:
            break

        print(f"[crawler] Fetching page {page_num}: {current_url}")
        html = get(current_url)
        soup = BeautifulSoup(html, "lxml")

        if page_num == 1:
            _save_local(html, "local_category_page.html")

        page_books = _extract_book_links(soup, current_url)
        book_urls.extend(page_books)
        print(f"[crawler]   Found {len(page_books)} book links on page {page_num}")

        if not page_books:
            # Empty page — stop early
            break

        current_url = _next_page_url(soup, current_url, page_num)

    return list(dict.fromkeys(book_urls))  # deduplicate, preserve order


def iter_book_links() -> Iterator[dict]:
    """Generator: yield one dict per discovered book.

    Yields:
        {"book_url": str, "source_category": str}
    """
    print("[crawler] Fetching homepage …")
    homepage_html = get(BASE_URL)
    _save_local(homepage_html, "local_homepage.html")

    categories = get_category_links(homepage_html)
    print(
        f"[crawler] Discovered {len(categories)} categories: "
        f"{[name for name, _ in categories]}"
    )

    first_book_saved = False
    for category_name, category_url in categories:
        print(f"[crawler] === Category: {category_name!r} — {category_url}")
        book_urls = get_book_links_from_category(category_url)
        print(f"[crawler] {len(book_urls)} book URLs in {category_name!r}")

        for book_url in book_urls:
            if not first_book_saved:
                _save_local(get(book_url), "local_book_page.html")
                first_book_saved = True
            yield {"book_url": book_url, "source_category": category_name}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _is_category_url(url: str) -> bool:
    """Return True if `url` looks like a top-level category listing page.

    Observed patterns on bookdelivery.com:
      Category listing : …/libros/{slug}   OR  …/books/{slug}
      Book page        : …/book-{slug}/{isbn}/p/{id}
      Author/Publisher : …/books/author/…  OR  …/books/editorial/…
    """
    path = urlparse(url).path.lower()

    exclude = [
        r"/book-",
        r"/books/author",
        r"/books/editorial",
        r"/books/publisher",
        r"/p/\d+",
        r"/cart", r"/checkout", r"/login", r"/register",
        r"/search", r"/account", r"/v2/",
        r"\.(pdf|jpg|png|gif|html)$",
    ]
    if any(re.search(pat, path) for pat in exclude):
        return False

    include = [
        r"^/libros/[^/]+$",   # /libros/{category-slug}
        r"^/books/[^/]+$",    # /books/{category-slug}  (after redirect)
        r"/il-en/books/[^/]+$",
    ]
    return any(re.search(pat, path) for pat in include)


def _extract_book_links(soup: BeautifulSoup, page_url: str) -> list[str]:
    """Return all book-page URLs found in `soup`.

    Book pages match: /book-{slug}/{isbn}/p/{numeric-id}
    """
    book_urls: list[str] = []
    for tag in soup.find_all("a", href=True):
        href: str = tag["href"].strip()
        absolute = urljoin(page_url, href)
        path = urlparse(absolute).path
        if re.search(r"/book-", path) and re.search(r"/p/\d+", path):
            book_urls.append(absolute)
    return book_urls


def _next_page_url(soup: BeautifulSoup, current_url: str, current_page: int) -> str | None:
    """Return the next pagination page URL, or None on the last page.

    bookdelivery.com uses ?page=N.  We look for a "next page" link first;
    if absent, we check whether a page N+1 link exists in the pagination;
    otherwise we return None (last page reached).
    """
    next_page = current_page + 1

    # 1. Explicit "next page" / "siguiente" / rel=next link
    for tag in soup.find_all("a", href=True):
        text = _clean_text(tag.get_text()).lower()
        rel = tag.get("rel", [])
        if "next" in rel or text in {"next page", "next", "siguiente", "›", "»", ">"}:
            return urljoin(current_url, tag["href"])

    # 2. Does a numbered link for next_page exist in the pagination block?
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if f"page={next_page}" in href:
            return urljoin(current_url, href)

    # 3. Build ?page=N+1 ourselves if page=N is in current URL
    parsed = urlparse(current_url)
    qs = parse_qs(parsed.query)
    if "page" in qs or current_page > 1:
        qs["page"] = [str(next_page)]
        new_query = urlencode({k: v[0] for k, v in qs.items()})
        return urlunparse(parsed._replace(query=new_query))

    # 4. First page had no ?page= yet — append ?page=2
    if current_page == 1:
        qs["page"] = ["2"]
        new_query = urlencode({k: v[0] for k, v in qs.items()})
        return urlunparse(parsed._replace(query=new_query))

    return None


def _clean_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _save_local(html: str, filename: str) -> None:
    """Persist HTML to disk for debugging."""
    import pathlib
    out = pathlib.Path("local_samples") / filename
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"[crawler] Saved {out}")
