"""Hunter.io client — find decision-maker emails for a company domain."""
from __future__ import annotations

from typing import Optional
import requests

from config.settings import HUNTER_API_KEY

BASE = "https://api.hunter.io/v2"

# Job-title priority for B2B juice sales. Higher score = better contact.
TITLE_PRIORITY = [
    ("f&b", 100), ("food and beverage", 100), ("food & beverage", 100),
    ("beverage director", 95), ("beverage manager", 90),
    ("general manager", 85), ("gm", 80),
    ("owner", 75), ("founder", 75), ("director", 70),
    ("operations", 65), ("procurement", 60), ("purchasing", 60),
    ("head chef", 55), ("executive chef", 55), ("chef", 50),
    ("manager", 45), ("marketing", 40),
]


def _score_title(title: Optional[str]) -> int:
    if not title:
        return 0
    t = title.lower()
    for kw, score in TITLE_PRIORITY:
        if kw in t:
            return score
    return 10


def domain_search(domain: str, limit: int = 25) -> list[dict]:
    """Return list of people at a domain: [{first_name, last_name, position, email, confidence}]."""
    if not HUNTER_API_KEY or not domain:
        return []
    try:
        resp = requests.get(
            f"{BASE}/domain-search",
            params={"domain": domain, "api_key": HUNTER_API_KEY, "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return [
            {
                "first_name": e.get("first_name"),
                "last_name": e.get("last_name"),
                "position": e.get("position"),
                "email": e.get("value"),
                "confidence": e.get("confidence", 0),
                "linkedin": e.get("linkedin"),
            }
            for e in data.get("emails", [])
            if e.get("value")
        ]
    except Exception:
        return []


def best_decision_maker(domain: str) -> Optional[dict]:
    """Return the single highest-priority decision-maker for a domain, or None."""
    people = domain_search(domain)
    if not people:
        return None
    # Score by job-title priority × confidence weight
    ranked = sorted(
        people,
        key=lambda p: (_score_title(p.get("position")), p.get("confidence", 0)),
        reverse=True,
    )
    top = ranked[0]
    # Only return if title looks like a real role (score > 10)
    if _score_title(top.get("position")) > 10:
        return top
    return None
