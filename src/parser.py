# Parses a single book page from bookdelivery.com into a flat dict.
# Most fields come from the JSON-LD Product block at the top of the page;
# the rest come from the Spanish "ficha" metadata table at the bottom.

from __future__ import annotations

import json
import math
import re

from bs4 import BeautifulSoup

# NIS-per-USD exchange rate. The site usually shows prices in NIS (₪);
# if a page happens to show only USD we use this rate to convert the other way.
USD_RATE = 3.01

EXPECTED_KEYS = [
    "Title",
    "Category",
    "Categories",
    "Authors",
    "PriceNIS",
    "PriceUSD",
    "Year",
    "Synopsis",
    "SynopsisLength",
    "StarRating",
    "NumberOfReviews",
    "Language",
    "Format",
    "Dimensions",
    "DimensionsUnit",
    "Weight",
    "WeightUnit",
    "ISBN",
    "ISBN13",
]


def ceil2(x: float) -> float:
    # Round up to 2 decimal places (assignment asks for ceiling, not normal rounding).
    return math.ceil(x * 100) / 100


def parse_book(html: str, source_category: str) -> dict:
    """Takes the raw HTML of a book page and the category it was found under,
    and returns a dict with all the fields we managed to find. Missing fields
    are left out of the dict completely (no None or empty string).
    """
    soup = BeautifulSoup(html, "lxml")
    ld = _find_product_ld(soup)

    record: dict = {"Category": source_category}

    title = _extract_title(soup, ld)
    if title is not None:
        record["Title"] = title

    authors = _extract_authors(ld, soup)
    if authors is not None:
        record["Authors"] = authors

    categories = _extract_categories(soup)
    if categories is not None:
        record["Categories"] = categories

    price, currency = _extract_price(ld, soup)
    if price is not None:
        if currency == "USD":
            # Page is in USD (rare). Convert the other way for NIS.
            record["PriceUSD"] = ceil2(price)
            record["PriceNIS"] = ceil2(price * USD_RATE)
        else:
            # Default: assume the price is in NIS (matches "ILS" and unknown).
            record["PriceNIS"] = ceil2(price)
            record["PriceUSD"] = ceil2(price / USD_RATE)

    year = _extract_year(soup)
    if year is not None:
        record["Year"] = year

    synopsis = _extract_synopsis(ld, soup)
    if synopsis is not None:
        record["Synopsis"] = synopsis
        record["SynopsisLength"] = len(synopsis)

    rating, number_of_reviews = _extract_star_rating(ld)
    if number_of_reviews is not None:
        record["NumberOfReviews"] = number_of_reviews
        record["StarRating"] = rating

    language = _extract_language(soup)
    if language is not None:
        record["Language"] = language

    book_format = _extract_format(soup)
    if book_format is not None:
        record["Format"] = book_format

    dimensions, dimensions_unit = _extract_dimensions(soup)
    if dimensions is not None:
        record["Dimensions"] = dimensions
    if dimensions_unit is not None:
        record["DimensionsUnit"] = dimensions_unit

    weight, weight_unit = _extract_weight(soup)
    if weight is not None:
        record["Weight"] = weight
    if weight_unit is not None:
        record["WeightUnit"] = weight_unit

    isbn, isbn13 = _extract_isbns(ld, soup)
    if isbn is not None:
        record["ISBN"] = isbn
    if isbn13 is not None:
        record["ISBN13"] = isbn13

    return record


def _find_product_ld(soup) -> dict | None:
    """Look through all the <script type="application/ld+json"> tags on the page
    and return the one that describes the Product (the book itself). Returns
    None if there isn't one, in which case we fall back to scraping the HTML.
    """
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") == "Product":
                return candidate
    return None


def _clean(text: str | None) -> str | None:
    # Collapse all whitespace runs to single spaces and trim. If the result is
    # empty (or input was None), return None so callers can treat it as missing.
    if text is None:
        return None
    cleaned = " ".join(text.split())
    return cleaned or None


def _extract_title(soup, ld) -> str | None:
    if ld and ld.get("name"):
        return _clean(ld["name"])
    element = soup.select_one("p.tituloProducto")
    return _clean(element.get_text()) if element is not None else None


def _extract_authors(ld, soup) -> str | None:
    # Authors can show up in the JSON-LD as either a list of {name: ...} dicts
    # or a single dict whose name field has multiple authors joined by " ; ".
    # We handle both, and if neither works we scrape the ficha block.
    if ld:
        author = ld.get("author")
        if isinstance(author, list):
            names = [
                _clean(author_entry.get("name"))
                for author_entry in author
                if isinstance(author_entry, dict)
            ]
            names = [name for name in names if name]
            if names:
                return ",".join(names)
        elif isinstance(author, dict):
            raw_name = _clean(author.get("name"))
            if raw_name:
                parts = [part.strip() for part in raw_name.split(";")]
                parts = [part for part in parts if part]
                return ",".join(parts)

    element = soup.find(id="metadata-Autor")
    if element is None:
        return None
    names = []
    for anchor in element.find_all("a"):
        if anchor.find("i") is not None:
            continue
        name = _clean(anchor.get_text())
        if name:
            names.append(name)
    return ",".join(names) if names else None


def _extract_categories(soup) -> str | None:
    element = soup.find(id="metadata-categorías")
    if element is None:
        return None
    categories = [_clean(anchor.get_text()) for anchor in element.find_all("a")]
    categories = [category for category in categories if category]
    return ",".join(categories) if categories else None


def _extract_price(ld, soup) -> tuple:
    # Returns (price, currency). Currency is "ILS", "USD", or None if we
    # couldn't tell. Prefer the JSON-LD offers price (which carries an explicit
    # priceCurrency); if that's missing, parse the visible price text inside
    # <strong class="precio"> and detect the currency from its symbol.
    if ld:
        offers = ld.get("offers")
        if isinstance(offers, list):
            offer_list = offers
        elif isinstance(offers, dict):
            offer_list = [offers]
        else:
            offer_list = []
        for offer in offer_list:
            if not isinstance(offer, dict):
                continue
            price = offer.get("price")
            if price is None:
                continue
            try:
                price_value = float(price)
            except (TypeError, ValueError):
                continue
            return (price_value, offer.get("priceCurrency"))

    element = soup.select_one("strong.precio")
    if element is None:
        return (None, None)
    text = element.get_text()
    if "₪" in text:
        currency = "ILS"
    elif "$" in text or "USD" in text.upper():
        currency = "USD"
    else:
        currency = None
    text = text.replace(",", "")
    for token in text.split():
        try:
            return (float(token), currency)
        except ValueError:
            continue
    return (None, None)


def _extract_year(soup) -> int | None:
    element = soup.find(id="metadata-ano")
    if element is None:
        return None
    text = _clean(element.get_text())
    if not text:
        return None
    # Find the first run of 4 consecutive digits and return it as the year.
    run = ""
    for char in text:
        if char.isdigit():
            run += char
            if len(run) == 4:
                return int(run)
        else:
            run = ""
    return None


def _extract_synopsis(ld, soup) -> str | None:
    if ld and ld.get("description"):
        return _clean(ld["description"])
    element = soup.find(id="texto-descripcion")
    return _clean(element.get_text()) if element is not None else None


def _extract_star_rating(ld) -> tuple:
    # Returns (rating, number_of_reviews).
    # If there are 0 reviews, rating is the string "None" (per the assignment).
    # If we can't even tell (no JSON-LD at all), returns (None, None) so the
    # caller knows to leave both fields out of the record entirely.
    if ld is None:
        return (None, None)
    aggregate = ld.get("aggregateRating")
    if not isinstance(aggregate, dict):
        return ("None", 0)
    try:
        review_count = int(
            aggregate.get("reviewCount") or aggregate.get("ratingCount") or 0
        )
    except (TypeError, ValueError):
        review_count = 0
    if review_count == 0:
        return ("None", 0)
    try:
        rating = float(aggregate.get("ratingValue"))
    except (TypeError, ValueError):
        return ("None", review_count)
    return (ceil2(rating), review_count)


def _extract_language(soup) -> str | None:
    element = soup.find(id="metadata-idioma")
    return _clean(element.get_text()) if element is not None else None


def _extract_format(soup) -> str | None:
    element = soup.find(id="metadata-encuadernación")
    return _clean(element.get_text()) if element is not None else None


def _extract_dimensions(soup) -> tuple:
    # The dimensions field looks like "20.1 x 13.2 x 2.3 cm.".
    # Pull all the numbers and the trailing unit separately.
    element = soup.find(id="metadata-dimensiones")
    if element is None:
        return (None, None)
    text = _clean(element.get_text())
    if not text:
        return (None, None)
    # Find every number in the string (integer or decimal).
    # e.g. "20.1 x 13.2 x 2.3 cm." -> ["20.1", "13.2", "2.3"]
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    # Match the trailing unit: one or more letters at the end of the string,
    # optionally followed by a period and trailing whitespace.
    # e.g. "20.1 x 13.2 x 2.3 cm." -> "cm"
    unit_match = re.search(r"([a-zA-Z]+)\.?\s*$", text)
    unit = unit_match.group(1) if unit_match else None
    dimensions = ",".join(numbers) if numbers else None
    return (dimensions, unit)


def _extract_weight(soup) -> tuple:
    element = soup.find(id="metadata-peso")
    if element is None:
        return (None, None)
    text = _clean(element.get_text())
    if not text:
        return (None, None)
    # Find the first number in the text. e.g. "0.27 kg" -> 0.27
    weight = None
    for token in text.split():
        try:
            weight = float(token)
            break
        except ValueError:
            continue
    # Match the trailing unit (letters at end of string, optional dot/space).
    # e.g. "0.27 kg" -> "kg"
    unit_match = re.search(r"([a-zA-Z]+)\.?\s*$", text)
    unit = unit_match.group(1) if unit_match else None
    return (weight, unit)


def _extract_isbns(ld, soup) -> tuple:
    # ISBN (10 digits) only lives in the ficha block.
    # ISBN13 prefers the ficha block, but if it's missing there we fall back
    # to the JSON-LD "isbn" field (which is always 13 digits on this site).
    isbn_element = soup.find(id="metadata-isbn")
    isbn13_element = soup.find(id="metadata-isbn13")
    isbn = _clean(isbn_element.get_text()) if isbn_element is not None else None
    isbn13 = _clean(isbn13_element.get_text()) if isbn13_element is not None else None

    if isbn13 is None and ld and ld.get("isbn"):
        candidate = _clean(str(ld["isbn"]))
        if candidate and len(candidate) == 13:
            isbn13 = candidate

    return (isbn, isbn13)
