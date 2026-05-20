"""Scrape current rental listings from realestate.com.au.

Uses the embedded __NEXT_DATA__ JSON blob (more reliable than DOM scraping).
realestate.com.au's rental search defaults to newest-listed first, which is
what we want.
"""
from __future__ import annotations

import json
import re
import time
from typing import Iterator, Optional

import httpx
from bs4 import BeautifulSoup

from .models import Listing

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

BASE = "https://www.realestate.com.au"


def _search_path(postcodes: list[str], min_rent: int) -> str:
    pc = "%2c+".join(postcodes)
    # realestate.com.au URL format: /rent/between-<min>-any-in-<postcodes>/list-<page>
    return f"/rent/between-{min_rent}-any-in-{pc}/list-{{page}}"


def _fetch_page(client: httpx.Client, path: str) -> str:
    r = client.get(BASE + path, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.text


def _extract_next_data(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        raise RuntimeError("__NEXT_DATA__ script not found on page")
    return json.loads(tag.string)


def _walk_for_listings(node) -> Iterator[dict]:
    """Recursively find listing-shaped dicts in the Next.js page data.

    A listing dict has a productDepth of 'standard'/'premiere' etc. and
    either an 'address' or 'listing' subkey. We look for objects that
    contain both 'address' and 'price' (or 'priceDetails').
    """
    if isinstance(node, dict):
        if (
            "address" in node
            and isinstance(node.get("address"), dict)
            and ("price" in node or "priceDetails" in node or "listingId" in node)
        ):
            yield node
        for v in node.values():
            yield from _walk_for_listings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_for_listings(v)


_RENT_RE = re.compile(r"\$\s*([\d,]+)")


def _parse_rent(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    m = _RENT_RE.search(raw)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _to_listing(node: dict) -> Optional[Listing]:
    addr = node.get("address") or {}
    listing_id = str(node.get("listingId") or node.get("id") or "")
    if not listing_id:
        return None

    street = addr.get("streetAddress") or addr.get("display", {}).get("shortAddress") or ""
    suburb = addr.get("suburb") or ""
    postcode = str(addr.get("postcode") or "")
    state = addr.get("state") or ""
    full = ", ".join(p for p in [street, suburb, f"{state} {postcode}".strip()] if p)

    price_raw = ""
    price = node.get("price") or node.get("priceDetails") or {}
    if isinstance(price, dict):
        price_raw = price.get("display") or price.get("displayPrice") or ""
    elif isinstance(price, str):
        price_raw = price

    features = node.get("generalFeatures") or {}
    def _count(key: str) -> Optional[int]:
        v = features.get(key)
        if isinstance(v, dict):
            v = v.get("value")
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    url = node.get("_links", {}).get("canonical", {}).get("href") if isinstance(node.get("_links"), dict) else None
    if not url:
        slug = node.get("slug") or ""
        url = f"{BASE}/property-{slug}" if slug else f"{BASE}/{listing_id}"

    date_listed = ""
    dates = node.get("dateAvailable") or node.get("listingCreatedTime") or node.get("dateListed")
    if isinstance(dates, str):
        date_listed = dates
    elif isinstance(dates, dict):
        date_listed = dates.get("value") or ""

    return Listing(
        listing_id=listing_id,
        listing_url=url if url.startswith("http") else BASE + url,
        property_address=full,
        suburb=suburb,
        postcode=postcode,
        weekly_rent=_parse_rent(price_raw),
        bedrooms=_count("bedrooms"),
        bathrooms=_count("bathrooms"),
        parking=_count("parkingSpaces") or _count("totalParking"),
        date_listed=date_listed,
    )


def fetch_listings(
    postcodes: list[str],
    min_rent: int,
    limit: Optional[int] = None,
    polite_delay: float = 1.5,
) -> list[Listing]:
    """Return all current rental listings matching the filters.

    Pages through results until an empty page is returned or `limit` is hit.
    """
    seen: set[str] = set()
    results: list[Listing] = []
    path_tpl = _search_path(postcodes, min_rent)

    with httpx.Client(follow_redirects=True) as client:
        page = 1
        while True:
            path = path_tpl.format(page=page)
            try:
                html = _fetch_page(client, path)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    break
                raise

            data = _extract_next_data(html)
            page_listings: list[Listing] = []
            for node in _walk_for_listings(data):
                lst = _to_listing(node)
                if lst is None or lst.listing_id in seen:
                    continue
                # Sanity filter: rent must be > min_rent and postcode must match
                if lst.postcode not in postcodes:
                    continue
                if lst.weekly_rent is not None and lst.weekly_rent < min_rent:
                    continue
                seen.add(lst.listing_id)
                page_listings.append(lst)
                results.append(lst)
                if limit and len(results) >= limit:
                    return results

            if not page_listings:
                break

            page += 1
            time.sleep(polite_delay)

    return results
