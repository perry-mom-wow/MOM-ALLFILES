"""Writer agent: generate the full outreach sequence for a prospect."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Literal

import anthropic

from agents.researcher import ProspectProfile
from config.settings import ANTHROPIC_API_KEY, load_icp, get_rep_by_id

ICP = load_icp()
_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


MessageType = Literal[
    "linkedin_connection",
    "linkedin_opener",
    "email_opener",
    "followup_day3",
    "followup_day7",
    "followup_day14",
    "reengage",
]


@dataclass
class OutreachMessage:
    message_type: MessageType
    channel: Literal["LinkedIn", "Email", "Instagram DM"]
    subject: str | None  # for email only
    body: str
    rep_id: str
    rep_name: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OutreachSequence:
    prospect_name: str
    venue_type: str
    tier: int
    rep_id: str
    messages: list[OutreachMessage]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


BRAND_CONTEXT = """\
Brand: mom-wow
Product: functional cold-press juices — premium, no additives, real ingredients, real results.
Origin: Portugal
USP: Not just juice. Functional nutrition in a bottle. Perfect for health-forward venues.
Tone: Warm, genuine, confident but never pushy. We believe in our product and know it fits premium venues.
"""

SYSTEM_PROMPT = f"""\
You are writing B2B sales outreach on behalf of a sales representative at mom-wow.

{BRAND_CONTEXT}

Rules:
- NEVER mention the CEO. Messages come from the sales rep only.
- Write in first person as the rep (their name, their voice, their tone).
- Keep LinkedIn messages SHORT (under 300 chars for connection request, under 500 chars for opener).
- Emails can be longer (200-350 words max).
- Lead with VALUE and personalisation — reference something specific about THEIR business.
- No generic templates. Every message must feel hand-written.
- Re-engagement messages should be funny, cheeky, and self-aware. Keep the brand warm even after silence.
- Respond ONLY with valid JSON.
"""


def _build_rep_context(rep: dict) -> str:
    samples = rep.get("sample_messages", [])
    sample_text = ""
    if samples:
        sample_text = "\nSample messages this rep has written:\n" + "\n---\n".join(samples)
    return (
        f"Rep name: {rep['name']}\n"
        f"Rep title: {rep['title']}\n"
        f"Tone: {rep.get('tone_notes', 'Professional and friendly')}\n"
        f"{sample_text}"
    )


def generate_sequence(profile: ProspectProfile, rep_id: str) -> OutreachSequence:
    """Generate the full outreach sequence for a prospect."""
    rep = get_rep_by_id(rep_id)
    if not rep:
        raise ValueError(f"Rep '{rep_id}' not found in reps.yaml")

    client = _get_client()
    rep_context = _build_rep_context(rep)

    prospect_context = f"""
Prospect: {profile.name}
Type: {profile.venue_type}
Location: {profile.address or 'Portugal'}
Website: {profile.website or 'none'}
Contact: {profile.contact_name or 'unknown'} ({profile.contact_title or 'unknown title'})
Description: {profile.description}
Personalisation hook: {profile.personalisation_hook}
Health/wellness angle: {profile.health_wellness_angle}
Tier: {profile.tier} (Tier 1 = €1K/mo, Tier 2 = €500-1K/mo, Tier 3 = €100-500/mo)
Has email: {'yes' if profile.email else 'no'}
Has LinkedIn: {'yes' if profile.linkedin_url else 'no'}
Has Instagram: {'yes' if profile.instagram_handle else 'no'}
"""

    user_content = f"""
{rep_context}

{prospect_context}

Generate the full outreach sequence. Respond with JSON in this exact format:
{{
  "linkedin_connection": "Short connection note (under 300 chars, no pitch)",
  "linkedin_opener": "Opening message after connection (under 500 chars, personalised)",
  "email_subject": "Email subject line (if they have an email)",
  "email_opener": "Full email body (200-350 words, personalised)",
  "followup_day3": "Day 3 follow-up (LinkedIn or email, 150-200 words, adds value)",
  "followup_day7": "Day 7 follow-up (social proof / story angle, 150 words)",
  "followup_day14": "Day 14 final touch (genuine, no pressure, 100 words)",
  "reengage": "5-week re-engagement (funny + cheeky + warm, 80-120 words)"
}}
"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data: dict = json.loads(raw)

    channel = "LinkedIn" if profile.linkedin_url else ("Email" if profile.email else "Instagram DM")

    messages = [
        OutreachMessage(
            message_type="linkedin_connection",
            channel="LinkedIn",
            subject=None,
            body=data.get("linkedin_connection", ""),
            rep_id=rep_id,
            rep_name=rep["name"],
        ),
        OutreachMessage(
            message_type="linkedin_opener",
            channel="LinkedIn",
            subject=None,
            body=data.get("linkedin_opener", ""),
            rep_id=rep_id,
            rep_name=rep["name"],
        ),
        OutreachMessage(
            message_type="email_opener",
            channel="Email",
            subject=data.get("email_subject"),
            body=data.get("email_opener", ""),
            rep_id=rep_id,
            rep_name=rep["name"],
        ),
        OutreachMessage(
            message_type="followup_day3",
            channel=channel,
            subject=None,
            body=data.get("followup_day3", ""),
            rep_id=rep_id,
            rep_name=rep["name"],
        ),
        OutreachMessage(
            message_type="followup_day7",
            channel=channel,
            subject=None,
            body=data.get("followup_day7", ""),
            rep_id=rep_id,
            rep_name=rep["name"],
        ),
        OutreachMessage(
            message_type="followup_day14",
            channel=channel,
            subject=None,
            body=data.get("followup_day14", ""),
            rep_id=rep_id,
            rep_name=rep["name"],
        ),
        OutreachMessage(
            message_type="reengage",
            channel=channel,
            subject=None,
            body=data.get("reengage", ""),
            rep_id=rep_id,
            rep_name=rep["name"],
        ),
    ]

    return OutreachSequence(
        prospect_name=profile.name,
        venue_type=profile.venue_type,
        tier=profile.tier,
        rep_id=rep_id,
        messages=messages,
    )


def generate_reengage_message(
    profile: ProspectProfile,
    rep_id: str,
    reengage_count: int,
) -> OutreachMessage:
    """Generate a fresh cheeky re-engagement message (for each 5-week cycle)."""
    rep = get_rep_by_id(rep_id)
    if not rep:
        raise ValueError(f"Rep '{rep_id}' not found")

    client = _get_client()
    rep_context = _build_rep_context(rep)

    user_content = f"""
{rep_context}

You're writing re-engagement #{reengage_count} to {profile.name} ({profile.venue_type}).
They haven't responded to any previous messages. This is 5 weeks since the last attempt.
Keep it funny, cheeky, warm, and very short (80-120 words).
Reference the fact that you keep trying without being annoying about it.
Make it human, make them smile. Don't hard-pitch. Just keep the door open.

Respond with JSON: {{"message": "..."}}
"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw)

    channel = "LinkedIn" if profile.linkedin_url else ("Email" if profile.email else "Instagram DM")
    return OutreachMessage(
        message_type="reengage",
        channel=channel,
        subject=None,
        body=data.get("message", ""),
        rep_id=rep_id,
        rep_name=rep["name"],
    )
