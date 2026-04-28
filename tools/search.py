"""Multi-source prospect search: Tavily web search + Google Maps Places."""
from __future__ import annotations

import time
import googlemaps
import requests
from config.settings import TAVILY_API_KEY, GOOGLE_MAPS_API_KEY


def tavily_search(query: str, max_results: int = 10) -> list[dict]:
    """Return a list of results from Tavily search API. Returns [] on failure (never raises)."""
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_answer": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 432:
            print(f"   ⚠️  Tavily rate limit hit — skipping this query")
            return []
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        print(f"   ⚠️  Tavily query failed ({e}) — skipping")
        return []


def google_maps_search(
    query: str,
    location: str,
    venue_type: str,
    max_results: int = 20,
) -> list[dict]:
    """Search Google Maps Places for venues of a given type near a location."""
    if not GOOGLE_MAPS_API_KEY:
        return []

    try:
        gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)
        geo = gmaps.geocode(location)
    except Exception:
        return []
    if not geo:
        return []
    lat_lng = geo[0]["geometry"]["location"]

    results = []
    page_token = None
    while len(results) < max_results:
        kwargs: dict = {
            "query": f"{venue_type} {query}",
            "location": (lat_lng["lat"], lat_lng["lng"]),
            "radius": 30000,  # 30km radius
        }
        if page_token:
            kwargs["page_token"] = page_token

        resp = gmaps.places(**kwargs)
        for place in resp.get("results", []):
            results.append({
                "name": place.get("name"),
                "address": place.get("formatted_address"),
                "place_id": place.get("place_id"),
                "rating": place.get("rating"),
                "user_ratings_total": place.get("user_ratings_total", 0),
                "website": None,
                "phone": None,
                "source": "google_maps",
            })

        page_token = resp.get("next_page_token")
        if not page_token or len(results) >= max_results:
            break
        time.sleep(2)  # Google requires a short delay before using page_token

    # Enrich top results with place details (website + phone)
    for i, r in enumerate(results[:max_results]):
        try:
            detail = gmaps.place(r["place_id"], fields=["website", "formatted_phone_number"])
            res = detail.get("result", {})
            r["website"] = res.get("website")
            r["phone"] = res.get("formatted_phone_number")
        except Exception:
            pass

    return results[:max_results]


def search_instagram_hashtag(hashtag: str, location: str) -> list[dict]:
    """Find venues via Tavily searching Instagram/social presence by hashtag+location."""
    query = f"site:instagram.com {hashtag} {location} beach club restaurant hotel gym"
    results = tavily_search(query, max_results=10)
    venues = []
    for r in results:
        url = r.get("url", "")
        if "instagram.com" in url:
            handle = url.rstrip("/").split("/")[-1]
            venues.append({
                "name": r.get("title", handle),
                "instagram_handle": handle,
                "instagram_url": url,
                "snippet": r.get("content", ""),
                "source": "instagram",
            })
    return venues


def find_linkedin_decision_maker(
    venue_name: str,
    location: str,
    country: str = "Portugal",
) -> Optional[dict]:
    """
    Tavily search for a decision-maker's LinkedIn profile.
    Strictly location-aware: results must mention the city OR country.
    Returns {name, title, linkedin_url} or None.
    """
    # Extract just the city from "Rua X, Lisbon, Portugal"
    city = location.split(",")[0].strip() if location else ""
    loc_signals = [s.lower() for s in [city, country, "lisboa", "lisbon"] if s]

    queries = [
        f'"{venue_name}" "{city}" (gerente OR diretor OR director OR manager OR owner) site:linkedin.com/in',
        f'"{venue_name}" {city} {country} general manager site:linkedin.com',
        f'"{venue_name}" {country} food beverage site:linkedin.com',
    ]
    seen = set()
    candidates = []
    for q in queries:
        for r in tavily_search(q, max_results=5):
            url = r.get("url", "")
            if "linkedin.com/in/" not in url or url in seen:
                continue
            seen.add(url)
            title = r.get("title", "")
            snippet = (r.get("content", "") or "").lower()
            # Need EITHER (a) venue name in title/snippet, OR (b) a location signal.
            # This catches valid matches where LinkedIn snippet is short or location implicit.
            blob = f"{title.lower()} {snippet}"
            has_venue = venue_name.lower() in blob
            has_location = any(sig in blob for sig in loc_signals)
            if not (has_venue or has_location):
                continue
            parts = [p.strip() for p in title.split(" - ")]
            name = parts[0] if parts else ""
            role = parts[1] if len(parts) > 1 else ""
            candidates.append({
                "name": name,
                "title": role,
                "linkedin_url": url,
                "snippet": r.get("content", "")[:300],
            })
    if not candidates:
        return None

    priority = ["f&b", "food", "beverage", "general manager", "owner",
                "director", "founder", "head", "operations", "manager", "gerente", "diretor"]
    def _score(c):
        t = (c.get("title") or "").lower()
        for i, kw in enumerate(priority):
            if kw in t:
                return len(priority) - i
        return 0
    candidates.sort(key=_score, reverse=True)
    top = candidates[0]
    return top if _score(top) > 0 else None


def search_directories(venue_type: str, location: str) -> list[dict]:
    """Find venues via Tavily searching TripAdvisor/TheFork/Yelp."""
    queries = [
        f"site:tripadvisor.com {venue_type} {location}",
        f"site:thefork.com {venue_type} {location}",
        f"best {venue_type} {location} contact",
    ]
    seen = set()
    venues = []
    for q in queries:
        for r in tavily_search(q, max_results=8):
            url = r.get("url", "")
            if url and url not in seen:
                seen.add(url)
                venues.append({
                    "name": r.get("title", ""),
                    "url": url,
                    "snippet": r.get("content", ""),
                    "source": "directory",
                })
    return venues
