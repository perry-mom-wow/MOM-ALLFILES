"""Discovery agent: find B2B prospects from multiple sources."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Literal

from tools.search import google_maps_search, tavily_search, search_instagram_hashtag, search_directories
from tools.scraper import scrape_url, extract_emails, extract_linkedin_url, extract_instagram_handle
from config.settings import load_icp

ICP = load_icp()

VenueType = Literal["beach_club", "restaurant", "cafe", "hotel", "gym", "wellness_center", "spa"]
Tier = Literal[1, 2, 3]


@dataclass
class ProspectRaw:
    name: str
    venue_type: VenueType
    address: str | None = None
    website: str | None = None
    phone: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    instagram_handle: str | None = None
    rating: float | None = None
    review_count: int = 0
    tier: Tier = 3
    source: str = "unknown"
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


VENUE_TYPE_QUERIES = {
    "beach_club": ["beach club", "beach bar", "chiringuito"],
    "restaurant": ["restaurant", "restaurante"],
    "cafe": ["café", "coffee shop", "juice bar"],
    "hotel": ["hotel", "resort", "boutique hotel"],
    "gym": ["gym", "fitness studio", "crossfit", "pilates studio"],
    "wellness_center": ["wellness center", "spa", "yoga studio"],
    "spa": ["spa", "beauty spa", "day spa"],
}


def _estimate_tier(place: dict, venue_type: str) -> Tier:
    """Estimate tier from rating count and venue type signals."""
    reviews = place.get("user_ratings_total", 0) or place.get("review_count", 0)
    name = (place.get("name") or "").lower()
    snippet = (place.get("snippet") or "").lower()

    # Hotel chains / multi-location = Tier 1
    if venue_type == "hotel" and reviews > 500:
        return 1
    if any(kw in name for kw in ["chain", "group", "resort", "collection"]):
        return 1
    if venue_type == "beach_club" and reviews > 200:
        return 1

    # Mid-size
    if reviews > 100:
        return 2
    if venue_type in ("hotel", "beach_club"):
        return 2

    return 3


def _revenue_for_tier(tier: Tier) -> float:
    tier_key = f"tier_{tier}"
    return ICP["tiers"][tier_key]["monthly_revenue_eur"]


def discover_prospects(
    location: str,
    venue_types: list[VenueType],
    max_per_type: int = 15,
) -> list[ProspectRaw]:
    """Discover prospects from all sources for given location and venue types."""
    prospects: dict[str, ProspectRaw] = {}  # keyed by name+address to dedupe

    for venue_type in venue_types:
        queries = VENUE_TYPE_QUERIES.get(venue_type, [venue_type])
        primary_query = queries[0]

        # 1. Google Maps (most reliable)
        gm_results = google_maps_search(primary_query, location, venue_type, max_results=max_per_type)
        for place in gm_results:
            key = _dedup_key(place.get("name", ""), place.get("address", ""))
            if key in prospects:
                continue
            tier = _estimate_tier(place, venue_type)
            p = ProspectRaw(
                name=place["name"],
                venue_type=venue_type,
                address=place.get("address"),
                website=place.get("website"),
                phone=place.get("phone"),
                tier=tier,
                rating=place.get("rating"),
                review_count=place.get("user_ratings_total", 0),
                source="google_maps",
            )
            prospects[key] = p

        # 2. Tavily web search
        for query in queries[:2]:
            results = tavily_search(f"{query} {location} contact", max_results=8)
            for r in results:
                name = r.get("title", "").strip()
                if not name or len(name) > 80:
                    continue
                key = _dedup_key(name, location)
                if key in prospects:
                    continue
                tier = _estimate_tier({"user_ratings_total": 0}, venue_type)
                p = ProspectRaw(
                    name=name,
                    venue_type=venue_type,
                    website=r.get("url"),
                    tier=tier,
                    source="tavily",
                    notes=r.get("content", "")[:300],
                )
                prospects[key] = p

        # 3. Directory search
        dir_results = search_directories(primary_query, location)
        for r in dir_results[:5]:
            name = r.get("name", "").strip()
            if not name or len(name) > 80:
                continue
            key = _dedup_key(name, location)
            if key not in prospects:
                prospects[key] = ProspectRaw(
                    name=name,
                    venue_type=venue_type,
                    website=r.get("url"),
                    tier=3,
                    source="directory",
                    notes=r.get("snippet", "")[:300],
                )

        # 4. Instagram hashtag search
        ig_results = search_instagram_hashtag(f"#{venue_type.replace('_', '')}", location)
        for r in ig_results[:5]:
            name = r.get("name", "").strip()
            if not name:
                continue
            key = _dedup_key(name, location)
            if key not in prospects:
                prospects[key] = ProspectRaw(
                    name=name,
                    venue_type=venue_type,
                    instagram_handle=r.get("instagram_handle"),
                    tier=3,
                    source="instagram",
                    notes=r.get("snippet", "")[:300],
                )

    result_list = list(prospects.values())

    # Enrich prospects that have a website: extract email, LinkedIn, Instagram
    for p in result_list:
        if p.website and not p.email:
            _enrich_from_website(p)

    return result_list


def _enrich_from_website(prospect: ProspectRaw) -> None:
    from tools.scraper import scrape_url, extract_emails, extract_linkedin_url, extract_instagram_handle
    text = scrape_url(prospect.website)
    emails = extract_emails(text)
    if emails:
        prospect.email = emails[0]
    if not prospect.linkedin_url:
        prospect.linkedin_url = extract_linkedin_url(text)
    if not prospect.instagram_handle:
        prospect.instagram_handle = extract_instagram_handle(text)


def _dedup_key(name: str, location: str) -> str:
    return re.sub(r"\W+", "", (name + location).lower())
