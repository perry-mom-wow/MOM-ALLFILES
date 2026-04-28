"""Researcher agent: deep-dive a prospect and produce a profile for the writer."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import anthropic

from agents.discovery import ProspectRaw
from config.settings import ANTHROPIC_API_KEY, load_icp
from tools.scraper import scrape_url, extract_emails, extract_linkedin_url, extract_instagram_handle
from tools.search import tavily_search, find_linkedin_decision_maker
from tools.hunter import best_decision_maker

ICP = load_icp()
_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


@dataclass
class ProspectProfile:
    # Basic
    name: str
    venue_type: str
    address: Optional[str]
    website: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    linkedin_url: Optional[str]
    instagram_handle: Optional[str]
    tier: int
    # Enriched
    contact_name: Optional[str] = None
    contact_title: Optional[str] = None
    description: str = ""
    personalisation_hook: str = ""
    health_wellness_angle: str = ""
    confirmed_tier: Optional[int] = None
    tier_reasoning: str = ""
    # Raw text gathered
    raw_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


SYSTEM_PROMPT = """\
You are a B2B sales researcher for MOM (the Longevity Alchemists), a premium functional cold-press juice brand in Portugal.

Your job is to analyse a venue and produce a structured prospect profile.
Focus on:
- What the venue does and who their customers are
- Any health / wellness / premium food & drink angle
- The key contact person (name + title) if visible
- A specific personalisation hook for cold outreach (something about THEIR business that makes the juice pitch relevant)
- Whether they are Tier 1 (€1K+/mo), Tier 2 (€500-1K/mo), or Tier 3 (€100-500/mo) based on size and footfall signals

Respond ONLY with valid JSON matching this schema:
{
  "contact_name": "string or null",
  "contact_title": "string or null",
  "description": "2-3 sentence summary of the venue",
  "personalisation_hook": "1-2 sentences: specific reason why cold-press juice fits THEIR business",
  "health_wellness_angle": "what health/wellness signals does this venue give off?",
  "confirmed_tier": 1 or 2 or 3,
  "tier_reasoning": "brief justification"
}
"""


def research_prospect(raw: ProspectRaw) -> ProspectProfile:
    """Enrich a raw prospect with deep research and Claude analysis."""
    # Gather text from multiple sources
    text_parts = []

    if raw.notes:
        text_parts.append(raw.notes)

    decision_maker: Optional[dict] = None
    if raw.website:
        page_text = scrape_url(raw.website)
        text_parts.append(page_text)
        # Try to find more contact info if missing
        if not raw.email:
            emails = extract_emails(page_text)
            if emails:
                raw.email = emails[0]
        if not raw.linkedin_url:
            raw.linkedin_url = extract_linkedin_url(page_text)
        if not raw.instagram_handle:
            raw.instagram_handle = extract_instagram_handle(page_text)

        # Hunter.io: find a real decision-maker email (overrides generic info@)
        domain = re.sub(r"^https?://(?:www\.)?", "", raw.website).split("/")[0]
        decision_maker = best_decision_maker(domain)
        if decision_maker:
            raw.email = decision_maker["email"]
            print(f"      ✓ Hunter found: {decision_maker.get('first_name')} {decision_maker.get('last_name')} — {decision_maker.get('position')}")

    # Fallback: if Hunter found nothing, search LinkedIn via Tavily
    if not decision_maker:
        li = find_linkedin_decision_maker(raw.name, raw.address or "")
        if li:
            decision_maker = {
                "first_name": li["name"].split(" ")[0] if li.get("name") else "",
                "last_name": " ".join(li["name"].split(" ")[1:]) if li.get("name") else "",
                "position": li.get("title"),
                "email": raw.email,  # keep generic email as fallback
                "linkedin": li.get("linkedin_url"),
            }
            print(f"      ✓ LinkedIn found: {li['name']} — {li.get('title')} ({li.get('linkedin_url')})")

    # Tavily search for extra context
    search_results = tavily_search(f"{raw.name} {raw.address or ''} menu health wellness contact", max_results=3)
    for r in search_results:
        text_parts.append(r.get("content", ""))

    raw_text = "\n\n".join(text_parts)[:10000]

    # Claude analysis with prompt caching on the system prompt
    user_content = f"""
Venue name: {raw.name}
Type: {raw.venue_type}
Address: {raw.address or 'unknown'}
Website: {raw.website or 'none'}
Estimated tier: {raw.tier}

Gathered information:
{raw_text}
"""

    client = _get_client()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
    )

    enriched: dict = {}
    try:
        text = response.content[0].text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        enriched = json.loads(text)
    except Exception:
        pass

    # Prefer Hunter's verified decision-maker over Claude's guess
    contact_name = enriched.get("contact_name")
    contact_title = enriched.get("contact_title")
    contact_linkedin = raw.linkedin_url
    if decision_maker:
        contact_name = f"{decision_maker.get('first_name','')} {decision_maker.get('last_name','')}".strip() or contact_name
        contact_title = decision_maker.get("position") or contact_title
        contact_linkedin = decision_maker.get("linkedin") or contact_linkedin

    return ProspectProfile(
        name=raw.name,
        venue_type=raw.venue_type,
        address=raw.address,
        website=raw.website,
        phone=raw.phone,
        email=raw.email,
        linkedin_url=contact_linkedin,
        instagram_handle=raw.instagram_handle,
        tier=enriched.get("confirmed_tier", raw.tier),
        contact_name=contact_name,
        contact_title=contact_title,
        description=enriched.get("description", ""),
        personalisation_hook=enriched.get("personalisation_hook", ""),
        health_wellness_angle=enriched.get("health_wellness_angle", ""),
        confirmed_tier=enriched.get("confirmed_tier", raw.tier),
        tier_reasoning=enriched.get("tier_reasoning", ""),
        raw_text=raw_text[:2000],
    )
