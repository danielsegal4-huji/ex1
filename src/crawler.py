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
import sys
import time
from datetime import datetime
from typing import Iterator
from urllib.parse import urljoin, urlparse, urlencode, urlunparse, parse_qs

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import InvalidSessionIdException, TimeoutException

BASE_URL = "https://www.bookdelivery.com/il-en/"
REQUEST_DELAY_SEC = 1
MAX_PAGES_PER_CATEGORY = 5

# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


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
    driver.set_page_load_timeout(60)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


_driver: webdriver.Chrome | None = None


def _get_driver() -> webdriver.Chrome:
    global _driver
    if _driver is None:
        _log("[crawler] Starting headless Chrome …")
        _driver = _make_driver()
    return _driver


def _reset_driver() -> None:
    """Quit the dead session (if any) and force a fresh Chrome on next use."""
    global _driver
    if _driver is not None:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None


def close_driver() -> None:
    """Call this when the crawl is done to release the browser process."""
    global _driver, _session
    if _driver is not None:
        _driver.quit()
        _driver = None
    if _session is not None:
        _session.close()
        _session = None


# ---------------------------------------------------------------------------
# Requests session (fast path)
# ---------------------------------------------------------------------------
# Strategy: the site is behind AWS WAF, which requires a JS challenge to be
# solved to obtain a session cookie. We use Selenium ONCE to solve the challenge
# and grab the cookies, then reuse those cookies with a plain `requests.Session`
# for every subsequent fetch. requests is roughly 10x faster than Selenium per
# page. If the cookie ever expires (WAF challenge HTML comes back), we transparently
# re-seed via Selenium and retry.

_session: requests.Session | None = None

# Substrings that appear in WAF challenge / block pages but not on real pages.
_WAF_BODY_MARKERS = (
    "aws waf",
    "challenge.js",
    "captcha-delivery",
    "/awswaf/",
)


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
    return _session


def _refresh_cookies_via_selenium(seed_url: str = BASE_URL) -> None:
    """Drive Selenium to seed_url so the WAF challenge is solved, then copy
    the resulting cookies + User-Agent into the requests.Session."""
    driver = _get_driver()
    _log(f"[crawler] Seeding cookies via Selenium: {seed_url}")
    driver.get(seed_url)
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    time.sleep(2)  # buffer for late-rendering JS / WAF cookie set
    session = _get_session()
    session.cookies.clear()
    for cookie in driver.get_cookies():
        session.cookies.set(cookie["name"], cookie["value"])
    session.headers["User-Agent"] = driver.execute_script("return navigator.userAgent")
    _log(f"[crawler] Got {len(session.cookies)} cookies; UA={session.headers['User-Agent'][:60]}...")


def _looks_like_waf_challenge(html: str, status_code: int) -> bool:
    if status_code in (403, 429, 503):
        return True
    head = html[:5000].lower()
    return any(marker in head for marker in _WAF_BODY_MARKERS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get(url: str) -> str:
    """Fetch `url` and return page HTML as text.

    Fast path: requests.Session with cookies seeded by Selenium (~300ms/page).
    Slow path: if the session has no cookies yet, or the response looks like a
    WAF challenge, re-seed via Selenium and retry once.

    Sleeps REQUEST_DELAY_SEC before each request (politeness).
    Retries up to 2 times on transient failures (network errors, blank pages).
    """
    max_retries = 2
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        _log(f"GET  [{attempt+1}/{max_retries+1}] sleeping {REQUEST_DELAY_SEC}s before: {url}")
        time.sleep(REQUEST_DELAY_SEC)
        try:
            session = _get_session()

            # First request ever - seed cookies via Selenium. Since we have to
            # load *something* in Chrome anyway, load the URL we actually want
            # and use its page_source directly.
            if len(session.cookies) == 0:
                _refresh_cookies_via_selenium(url)
                html = _get_driver().page_source
                if len(html) > 500:
                    _log(f"GET  OK (selenium seed)  {len(html):,} chars - {url}")
                    return html
                last_exc = RuntimeError(f"Seed page too short ({len(html)} chars): {url}")
                continue

            # Fast path: HTTP request with cached WAF cookies.
            response = session.get(url, timeout=30)
            html = response.text

            if _looks_like_waf_challenge(html, response.status_code):
                _log(f"GET  WAF challenge detected (status={response.status_code}), re-seeding ...")
                _refresh_cookies_via_selenium(BASE_URL)
                response = session.get(url, timeout=30)
                html = response.text
                if _looks_like_waf_challenge(html, response.status_code):
                    last_exc = RuntimeError(
                        f"WAF challenge persists after reseed (status={response.status_code})"
                    )
                    continue

            if len(html) > 500:
                _log(f"GET  OK (requests)  {len(html):,} chars - {url}")
                return html
            last_exc = RuntimeError(f"Page too short ({len(html)} chars): {url}")
            _log(f"GET  [{attempt+1}/{max_retries+1}] page too short ({len(html)} chars)")
        except InvalidSessionIdException as exc:
            last_exc = exc
            _log(f"GET  Chrome session died on {url!r}, resetting driver ...")
            _reset_driver()
        except TimeoutException as exc:
            last_exc = exc
            _log(f"GET  JS timeout on {url!r}, resetting driver ...")
            _reset_driver()
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            _log(f"GET  HTTP error {type(exc).__name__}: {exc}")
        except Exception as exc:
            last_exc = exc
            _log(f"GET  ERROR {type(exc).__name__}: {exc}")

        if attempt < max_retries:
            backoff = 2 ** (attempt + 1)
            _log(f"GET  retrying in {backoff}s ...")
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

        _log(f"[crawler] Fetching page {page_num}: {current_url}")
        html = get(current_url)
        soup = BeautifulSoup(html, "lxml")

        if page_num == 1:
            _save_local(html, "local_category_page.html")

        page_books = _extract_book_links(soup, current_url)
        book_urls.extend(page_books)
        _log(f"[crawler]   Found {len(page_books)} book links on page {page_num}")

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
    _log("[crawler] Fetching homepage …")
    homepage_html = get(BASE_URL)
    _save_local(homepage_html, "local_homepage.html")

    categories = get_category_links(homepage_html)
    print(
        f"[crawler] Discovered {len(categories)} categories: "
        f"{[name for name, _ in categories]}"
    )

    first_book_saved = False
    for category_name, category_url in categories:
        _log(f"[crawler] === Category: {category_name!r} — {category_url}")
        book_urls = get_book_links_from_category(category_url)
        _log(f"[crawler] {len(book_urls)} book URLs in {category_name!r}")

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
    _log(f"[crawler] Saved {out}")
