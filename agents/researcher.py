"""Researcher agent: deep-dive a prospect and produce a profile for the writer."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict

import anthropic

from agents.discovery import ProspectRaw
from config.settings import ANTHROPIC_API_KEY, load_icp
from tools.scraper import scrape_url, extract_emails, extract_linkedin_url, extract_instagram_handle
from tools.search import tavily_search

ICP = load_icp()
_client: anthropic.Anthropic | None = None


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
    address: str | None
    website: str | None
    phone: str | None
    email: str | None
    linkedin_url: str | None
    instagram_handle: str | None
    tier: int
    # Enriched
    contact_name: str | None = None
    contact_title: str | None = None
    description: str = ""
    personalisation_hook: str = ""
    health_wellness_angle: str = ""
    confirmed_tier: int | None = None
    tier_reasoning: str = ""
    # Raw text gathered
    raw_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


SYSTEM_PROMPT = """\
You are a B2B sales researcher for mom-wow, a premium functional cold-press juice brand in Portugal.

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

    return ProspectProfile(
        name=raw.name,
        venue_type=raw.venue_type,
        address=raw.address,
        website=raw.website,
        phone=raw.phone,
        email=raw.email,
        linkedin_url=raw.linkedin_url,
        instagram_handle=raw.instagram_handle,
        tier=enriched.get("confirmed_tier", raw.tier),
        contact_name=enriched.get("contact_name"),
        contact_title=enriched.get("contact_title"),
        description=enriched.get("description", ""),
        personalisation_hook=enriched.get("personalisation_hook", ""),
        health_wellness_angle=enriched.get("health_wellness_angle", ""),
        confirmed_tier=enriched.get("confirmed_tier", raw.tier),
        tier_reasoning=enriched.get("tier_reasoning", ""),
        raw_text=raw_text[:2000],
    )
