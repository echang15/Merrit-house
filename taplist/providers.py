"""Online beer search providers.

Each provider exposes `search(query) -> list[dict]` where every dict has the
shape used across the app:

    {name, brewery, style, abv, ibu, description, label_url, source, source_id}

Providers:
  * Open Food Facts - free, no API key, decent label photos. Enabled by default.
  * Catalog.beer   - open beer database with clean style/ABV/IBU data and
                      descriptions; needs a free API key (TAPLIST_CATALOG_BEER_KEY).
  * Untappd        - excellent descriptions/labels but needs API credentials
                      (TAPLIST_UNTAPPD_CLIENT_ID / TAPLIST_UNTAPPD_CLIENT_SECRET).
"""

import logging
import os
import re

import requests

log = logging.getLogger(__name__)

USER_AGENT = "MerrittHouseTapList/1.0 (home tap display; https://github.com/echang15/merritt-house)"
TIMEOUT = float(os.environ.get("TAPLIST_SEARCH_TIMEOUT", "8"))

# Open Food Facts category tags -> human readable style. Order matters: first match wins.
_OFF_STYLES = [
    ("imperial-stout", "Imperial Stout"),
    ("stout", "Stout"),
    ("porter", "Porter"),
    ("double-ipa", "Double IPA"),
    ("india-pale-ale", "IPA"),
    ("ipa", "IPA"),
    ("pale-ale", "Pale Ale"),
    ("pilsner", "Pilsner"),
    ("pils", "Pilsner"),
    ("lager", "Lager"),
    ("wheat", "Wheat Beer"),
    ("weizen", "Hefeweizen"),
    ("witbier", "Witbier"),
    ("blanche", "Witbier"),
    ("saison", "Saison"),
    ("sour", "Sour"),
    ("gose", "Gose"),
    ("lambic", "Lambic"),
    ("tripel", "Tripel"),
    ("dubbel", "Dubbel"),
    ("quadrupel", "Quadrupel"),
    ("abbey", "Abbey Ale"),
    ("trappist", "Trappist Ale"),
    ("amber", "Amber Ale"),
    ("red-ale", "Red Ale"),
    ("brown-ale", "Brown Ale"),
    ("bock", "Bock"),
    ("kolsch", "Kölsch"),
    ("cider", "Cider"),
    ("blonde", "Blonde Ale"),
    ("ale", "Ale"),
    ("beer", "Beer"),
]


def _style_from_tags(tags):
    tags = [t.split(":", 1)[-1] for t in (tags or [])]
    for needle, label in _OFF_STYLES:
        for t in tags:
            if needle in t:
                return label
    return ""


def _clean_text(text):
    if not text:
        return ""
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text


class OpenFoodFacts:
    name = "openfoodfacts"
    label = "Open Food Facts"
    URL = "https://world.openfoodfacts.org/cgi/search.pl"

    def search(self, query, limit=12):
        params = {
            "search_terms": query,
            "search_simple": 1,
            "action": "process",
            "json": 1,
            "page_size": limit,
            "tagtype_0": "categories",
            "tag_contains_0": "contains",
            "tag_0": "beers",
            "fields": "code,product_name,product_name_en,brands,generic_name,generic_name_en,"
                      "image_front_url,image_front_small_url,categories_tags,nutriments,"
                      "ingredients_text,ingredients_text_en,quantity",
        }
        resp = requests.get(self.URL, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        payload = resp.json()
        results = []
        for p in payload.get("products", []):
            name = _clean_text(p.get("product_name_en") or p.get("product_name"))
            if not name:
                continue
            nutriments = p.get("nutriments") or {}
            abv = nutriments.get("alcohol_100g") or nutriments.get("alcohol_value") or nutriments.get("alcohol")
            try:
                abv = float(abv) if abv not in (None, "") else None
            except (TypeError, ValueError):
                abv = None
            description = _clean_text(p.get("generic_name_en") or p.get("generic_name"))
            ingredients = _clean_text(p.get("ingredients_text_en") or p.get("ingredients_text"))
            if ingredients and ingredients.lower() != description.lower():
                description = f"{description}\n\nIngredients: {ingredients}" if description else f"Ingredients: {ingredients}"
            results.append(
                {
                    "name": name,
                    "brewery": _clean_text((p.get("brands") or "").split(",")[0]),
                    "style": _style_from_tags(p.get("categories_tags")),
                    "abv": abv,
                    "ibu": None,
                    "description": description,
                    "label_url": p.get("image_front_url") or p.get("image_front_small_url"),
                    "source": self.name,
                    "source_id": str(p.get("code") or ""),
                }
            )
        return results


class Untappd:
    name = "untappd"
    label = "Untappd"
    URL = "https://api.untappd.com/v4/search/beer"

    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret

    def search(self, query, limit=12):
        params = {"q": query, "limit": limit, "client_id": self.client_id, "client_secret": self.client_secret}
        resp = requests.get(self.URL, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        items = (((resp.json() or {}).get("response") or {}).get("beers") or {}).get("items") or []
        results = []
        for item in items:
            beer = item.get("beer") or {}
            brewery = item.get("brewery") or {}
            if not beer.get("beer_name"):
                continue
            results.append(
                {
                    "name": _clean_text(beer.get("beer_name")),
                    "brewery": _clean_text(brewery.get("brewery_name")),
                    "style": _clean_text(beer.get("beer_style")),
                    "abv": beer.get("beer_abv") or None,
                    "ibu": beer.get("beer_ibu") or None,
                    "description": _clean_text(beer.get("beer_description")),
                    "label_url": beer.get("beer_label_hd") or beer.get("beer_label"),
                    "brewery_logo_url": brewery.get("brewery_label"),
                    "source": self.name,
                    "source_id": str(beer.get("bid") or ""),
                }
            )
        return results


OPEN_BREWERY_DB = "https://api.openbrewerydb.org/v1/breweries/search"
FAVICON_SERVICE = "https://www.google.com/s2/favicons?domain={host}&sz=256"


def _site_icon(url):
    """Best-effort logo for a brewery from its website: the 256px site icon."""
    host = re.sub(r"^https?://", "", (url or "").strip()).split("/")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return FAVICON_SERVICE.format(host=host) if host else None


class CatalogBeer:
    """https://catalog.beer - an open, community-maintained beer database.

    Every request needs an API key (free from the Account page on catalog.beer),
    sent as the username of HTTP Basic auth with an empty password. Search hits
    come back as full beer objects with the brewer nested, so one call is enough.
    There is no artwork; the brewer's site icon stands in for a logo.
    """

    name = "catalogbeer"
    label = "Catalog.beer"
    URL = "https://api.catalog.beer/beer/search"

    def __init__(self, api_key):
        self.api_key = api_key

    def search(self, query, limit=12):
        resp = requests.get(
            self.URL,
            params={"q": query, "count": max(1, min(int(limit), 100))},
            auth=(self.api_key, ""),
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        if payload.get("error"):
            raise RuntimeError(payload.get("error_msg") or "Catalog.beer returned an error")
        results = []
        for beer in payload.get("data") or []:
            name = _clean_text(beer.get("name"))
            if not name:
                continue
            brewer = beer.get("brewer") or {}
            results.append(
                {
                    "name": name,
                    "brewery": _clean_text(brewer.get("name")),
                    "style": _clean_text(beer.get("style")),
                    "abv": _num_or_none(beer.get("abv")),
                    "ibu": _num_or_none(beer.get("ibu")),
                    "description": _clean_text(beer.get("description")),
                    "label_url": None,
                    "brewery_logo_url": _site_icon(brewer.get("url")),
                    "source": self.name,
                    "source_id": str(beer.get("id") or ""),
                }
            )
        return results


def _num_or_none(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def find_brewery_logos(query, limit=6):
    """Look a brewery up on Open Brewery DB and return logo candidates.

    Open Brewery DB has no artwork, but it knows the brewery's website, and a
    256px site icon is usually the brewery's logo. Good enough as a one-tap
    fallback; the user can always upload something better.
    """
    resp = requests.get(OPEN_BREWERY_DB, params={"query": query, "per_page": limit},
                        timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    out = []
    for b in resp.json() or []:
        site = (b.get("website_url") or "").strip()
        logo = _site_icon(site)
        if not logo:
            continue
        place = ", ".join(x for x in (b.get("city"), b.get("state_province") or b.get("state"), b.get("country")) if x)
        out.append({
            "brewery": _clean_text(b.get("name")),
            "place": place,
            "website": site,
            "logo_url": logo,
        })
    return out


def providers_from_env():
    """Build the enabled provider list from environment variables."""
    enabled = []
    if os.environ.get("TAPLIST_DISABLE_OFF", "").lower() not in ("1", "true", "yes"):
        enabled.append(OpenFoodFacts())
    key = os.environ.get("TAPLIST_CATALOG_BEER_KEY", "").strip()
    if key:
        enabled.insert(0, CatalogBeer(key))
    cid = os.environ.get("TAPLIST_UNTAPPD_CLIENT_ID")
    secret = os.environ.get("TAPLIST_UNTAPPD_CLIENT_SECRET")
    if cid and secret:
        enabled.insert(0, Untappd(cid, secret))
    return enabled


def search_all(providers, query, limit=12):
    """Query every provider, tolerating individual failures. Returns (results, errors)."""
    results, errors = [], []
    for provider in providers:
        try:
            results.extend(provider.search(query, limit=limit))
        except Exception as exc:  # network errors, bad JSON, rate limits...
            log.warning("provider %s failed: %s", provider.name, exc)
            errors.append({"source": provider.name, "label": provider.label, "error": str(exc)})
    return results, errors
