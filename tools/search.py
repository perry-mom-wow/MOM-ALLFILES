"""Multi-source prospect search: Tavily web search + Google Maps Places."""
from __future__ import annotations

import time
import googlemaps
import requests
from config.settings import TAVILY_API_KEY, GOOGLE_MAPS_API_KEY


def tavily_search(query: str, max_results: int = 10) -> list[dict]:
    """Return a list of results from Tavily search API."""
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_answer": False,
    }
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("results", [])


def google_maps_search(
    query: str,
    location: str,
    venue_type: str,
    max_results: int = 20,
) -> list[dict]:
    """Search Google Maps Places for venues of a given type near a location."""
    if not GOOGLE_MAPS_API_KEY:
        return []

    gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)

    # Geocode the location first
    geo = gmaps.geocode(location)
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
