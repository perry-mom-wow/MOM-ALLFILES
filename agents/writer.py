"""Writer agent: generate the full outreach sequence for a prospect."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Literal, Optional

import anthropic

from agents.researcher import ProspectProfile
from config.settings import ANTHROPIC_API_KEY, load_icp, get_rep_by_id

ICP = load_icp()
_client: Optional[anthropic.Anthropic] = None


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
    subject: Optional[str]  # for email only
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
Brand: MOM, by the Longevity Alchemists. (Website: mom-wow.com — but the brand is MOM.)
We are not a juice company. We are Longevity Alchemists. Cold-press juice is our delivery system.

Brand naming rules:
- Always write the brand as MOM (uppercase). Never "Mom", "mom-wow", "mom wow", or "MOM-wow".
- "mom-wow" only appears in the URL or email address (mom-wow.com).
- When introducing the brand, say "MOM" or "MOM, by the Longevity Alchemists".

What we make:
- Ready-to-open, grab-and-go cold-pressed juices.
- Real fruits, real vegetables, real roots, supported by medicinal mushrooms (lion's mane, reishi, chaga, cordyceps).
- Cold-pressed and HPP-treated for a 60-day shelf life (versus 3-5 days for typical cold-press).
- Organic ingredients, no additives, no shortcuts.
- Strong wholesale margin for venues.

Why venues choose us:
- ZERO prep. ZERO blender. ZERO cleanup. Open the bottle, pour, serve.
- Solves the pain of trying to deliver a fresh wellness option when the kitchen is slammed and short-staffed.
- Premium positioning that fits beside natural wine, kombucha, specialty coffee.
- Longevity is now mainstream. Guests are asking for it. We're the easiest way to say yes.

Origin: Portugal.
"""

SYSTEM_PROMPT = f"""\
You are writing B2B sales outreach on behalf of a sales representative for MOM, the Longevity Alchemists.

{BRAND_CONTEXT}

═══════════════════════════════════════════════════════════════
HOW TO OPEN — THIS IS THE SINGLE MOST IMPORTANT RULE
═══════════════════════════════════════════════════════════════

LEAD WITH EMPATHY. LEAD WITH THE PROSPECT'S WORLD. NEVER OPEN WITH WHAT WE MAKE.

Your first 1-2 sentences must make the reader feel SEEN — surface a real
operational pain or aspiration that someone in their role faces every week:
- Staffing pressure during peak service
- Pressure to add a wellness offer without adding a barista
- Guests asking for healthier options the team has no time to prep
- The ambition to be the venue people associate with longevity / quality of life

ONLY AFTER the empathetic opener should you introduce MOM, and even then
position us as a SOLUTION to that pain — not as a product line.

Example shape (DO NOT copy verbatim — this is structure only):
  "[Empathy / pain observation specific to THEIR business in 1-2 sentences].
   We make ready-to-open cold-pressed juices powered by medicinal mushrooms,
   so [their specific outcome — e.g. you can serve a real wellness option
   even when the kitchen is slammed]. [Longevity positioning, 1 line]."

═══════════════════════════════════════════════════════════════
WRITING RULES
═══════════════════════════════════════════════════════════════

- NEVER refuse to write a message. NEVER output "N/A", "no LinkedIn found",
  "use Instagram DM instead", "no profile available", or any meta-commentary.
  Every field MUST contain a real, sendable, personalised message.
  The rep handles channel selection downstream — your job is to write copy that
  works on ANY channel (LinkedIn / email / Instagram DM / in-person handoff).
  WRONG: "N/A — no LinkedIn profile found for this contact."
  RIGHT: "Hey, love what you're doing at [venue]. [Real personalised message]."
- NEVER mention the CEO. Messages come from the sales rep only.
- Write in first person as the rep.
- LinkedIn connection request: under 300 chars.
- LinkedIn opener: under 500 chars.
- Emails: 200-350 words max.
- Every message must reference something SPECIFIC about THEIR business.
- No generic templates. Hand-written feel.
- Re-engagement messages: funny, cheeky, self-aware. Warm even after silence.

═══════════════════════════════════════════════════════════════
PUNCTUATION RULES (strict — deal-breakers)
═══════════════════════════════════════════════════════════════

- NEVER use em dashes (—) or en dashes (–). Use a comma, full stop, or "and".
- NEVER use double hyphens (--).
- Simple punctuation a human types on a phone: commas, full stops, question marks.
- Avoid AI-tells: "I hope this finds you well", "in today's fast-paced world",
  "circle back", "leverage", "synergy", "unlock", "ecosystem", "exciting opportunity".
- Avoid stock juice-pitch phrases: "real ingredients, real flavour", "no additives",
  "we make functional juice", "premium quality" as opener.

═══════════════════════════════════════════════════════════════
PRODUCT FACTS (use sparingly, pick what fits)
═══════════════════════════════════════════════════════════════

Pick the ONE OR TWO most relevant to the venue's specific situation:
- Ready-to-open / grab-and-go (no prep, no blender, no waste) — for busy ops
- 60-day shelf life — for venues that hate spoilage and complex ordering
- Medicinal mushrooms (lion's mane, reishi, etc.) — for wellness / longevity-led venues
- Strong wholesale margin — for F&B Directors and GMs who own the P&L
- Organic, real fruits / veg / roots — for premium / natural-positioning venues

NEVER dump all five in one message.

Respond ONLY with valid JSON.
"""


def _build_rep_context(rep: dict) -> str:
    samples = rep.get("sample_messages", [])
    sample_text = ""
    if samples:
        sample_text = (
            "\n\n═══ THIS REP'S ACTUAL VOICE (study these samples carefully) ═══\n"
            "These are real messages this rep has written. MIRROR their:\n"
            "- sentence length and rhythm\n"
            "- punctuation habits (some use lots of commas, some use full stops)\n"
            "- vocabulary quirks and signature phrases\n"
            "- formality level (formal vs casual, business vs friendly)\n"
            "- typos and natural human imperfections (don't over-polish)\n"
            "- typical sign-off style\n\n"
            "SAMPLES:\n"
            + "\n---\n".join(samples)
            + "\n═══════════════════════════════════════════════════════════════\n"
            "Your output MUST sound like the same person wrote it."
        )
    return (
        f"Rep name: {rep['name']}\n"
        f"Rep title: {rep['title']}\n"
        f"Tone notes: {rep.get('tone_notes', 'Professional and friendly')}\n"
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
