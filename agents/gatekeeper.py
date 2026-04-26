"""Gatekeeper: final-line validation before any prospect is written to HubSpot."""
from __future__ import annotations

import json
from typing import Optional

import anthropic

from agents.researcher import ProspectProfile
from config.settings import ANTHROPIC_API_KEY, load_icp

ICP = load_icp()
TARGET_COUNTRY = ICP.get("brand", {}).get("country", "Portugal")
_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


GATEKEEPER_SYSTEM = """You are the final gatekeeper for a Portuguese B2B juice brand's CRM.

Decide if this prospect is worth adding to the CRM. Reject if ANY of these apply:
1. Name looks like an article title, listicle, directory listing, or marketing page
   (e.g. "Best 30 restaurants...", "Welcome to...", "Restaurants in...", "Contact X - Reserve...")
2. The venue is not in {country}
3. The venue doesn't appear to actually exist as a single, real establishment
4. The "venue" is actually a media outlet, blog, aggregator, or hotel chain corporate page
5. The venue type doesn't match what we sell to (we sell to: restaurant, beach_club, cafe, hotel,
   gym, wellness_center, spa)

Otherwise: ACCEPT.

Respond with VALID JSON ONLY:
{"accept": true|false, "reason": "short explanation if rejected, else empty"}
"""


def validate_prospect(profile: ProspectProfile) -> tuple[bool, str]:
    """
    Validate a prospect profile before HubSpot insertion.
    Returns (is_valid, reason). On any failure, defaults to ACCEPT to avoid blocking.
    """
    if not ANTHROPIC_API_KEY:
        return True, ""

    payload = {
        "name": profile.name,
        "venue_type": profile.venue_type,
        "address": profile.address or "unknown",
        "website": profile.website or "",
        "description": (profile.description or "")[:400],
    }

    try:
        response = _get_client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            system=[{
                "type": "text",
                "text": GATEKEEPER_SYSTEM.replace("{country}", TARGET_COUNTRY),
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        decision = json.loads(text)
        return bool(decision.get("accept", True)), str(decision.get("reason", ""))
    except Exception:
        # On any error, accept (don't block real prospects due to API issues)
        return True, ""
