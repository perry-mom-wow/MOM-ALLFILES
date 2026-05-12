"""Writer agent: generate the full outreach sequence for a prospect."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Literal, Optional

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

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
COLD EMAIL OPENER — THE 10 COMMANDMENTS (canonical, see Wiki)
═══════════════════════════════════════════════════════════════

The `email_opener` and `email_subject` fields are governed by these rules.
The Wiki page "📨 Cold Email Construction Rules" is the canonical source. Empirical
basis: Boomerang (40M emails), Backlinko (12M), Gong (85M), Belkins (16.5M),
Lavender. These rules ARE NOT NEGOTIABLE.

1. **50–75 words. 3–4 sentences. ONE idea. Hard cap 100 words.**
   Past 150 = reply-rate cliff. Aim short, not long.
2. **Subject: 1–5 words, ALL LOWERCASE, never marketing-shaped.**
   No "MOM x Venue", no "Partnership opportunity", no "Quick note". Hint at the
   specific observation in line 1 of the body. Good: `cold-pressed margins`,
   `saw your Anjos opening`, `quick thought on Sunday brunch`.
3. **Line 1 = a specific observation about THEM.** A press piece, new opening,
   recent hire, menu change, event, funding. NEVER "I hope this finds you well".
   NEVER restate their title (it's in their signature).
4. **Lines 2–3 = problem + credibility with a NUMBER.**
   Concrete > abstract. Example: "Most operators we work with lose ~30% of
   cold-pressed product to spoilage. We cut that to 18% for [Comparable Venue]."
5. **Line 4 = interest CTA ending in a QUESTION.**
   Never a calendar link, never "do you have 15 min". Good: "Worth comparing
   notes?" / "Open to me sending the 2-page sheet?" / "Are you cold-pressing
   in-house or sourcing?"
6. **Plain text. ONE link max. No images. No attachments.**
   Signature = name + role + one URL.
7. **3rd-grade reading level.** Short words. Short sentences. No nested
   clauses, no adverbs, no jargon, no buzzwords.
8. **Named comparables and specific numbers beat adjectives every time.**
9. **No calendar link, no attachment, no `unsubscribe` boilerplate on touch #1.**
10. **P.S. line allowed and encouraged.** Most-read part of an email. Use for a
    low-stakes proof-point or a soft "no worries if not the right time."

EMAIL_OPENER STRUCTURE (do NOT copy verbatim — match the shape):

  Hi [first name],
  [Specific observation about their venue, recent move, or moment]. [Pain
  most operators like them face, with one number]. [What we did for a
  named comparable venue, with the result number].
  [Interest CTA question.]
  [Rep's first name]
  [Rep's title], MOM
  mom-wow.com
  P.S. [optional, low-stakes proof point or permission line.]

═══════════════════════════════════════════════════════════════
LINKEDIN + FOLLOW-UPS — separate rules
═══════════════════════════════════════════════════════════════

- LinkedIn connection request: under 300 chars. Single observation + soft ask.
- LinkedIn opener (after connection accepted): under 500 chars. Same shape as
  the email body but tighter — no signature, no P.S.
- Followups (day 3/7/14): 80–150 words each. NEW angle, NEW number, NEW
  comparable. Never "just bumping this up".
- Re-engage (week 5+): use Dean Jackson's 9-word email pattern as the model:
  one line, single question, easy yes/no/not-yet reply gradient.

═══════════════════════════════════════════════════════════════
WRITING RULES (apply to every message)
═══════════════════════════════════════════════════════════════

- NEVER refuse to write a message. NEVER output "N/A", "no LinkedIn found",
  "use Instagram DM instead", or any meta-commentary. Every field MUST
  contain a real, sendable, personalised message.
- NEVER mention the CEO. Messages come from the sales rep only.
- Write in first person as the rep.
- Every message references something SPECIFIC about THEIR business.
- Hand-written feel. No template language.

═══════════════════════════════════════════════════════════════
PUNCTUATION (strict — deal-breakers)
═══════════════════════════════════════════════════════════════

- NEVER em dashes (—) or en dashes (–). Use commas, full stops, or "and".
- NEVER double hyphens (--).
- Simple punctuation only.
- BANNED phrases (AI-tells): "I hope this finds you well", "in today's
  fast-paced world", "circle back", "circling back", "touching base",
  "leverage", "synergy", "unlock", "ecosystem", "exciting opportunity",
  "quick question", "I wanted to reach out", "my name is".
- BANNED spam triggers: "free", "guaranteed", "act now", "limited time",
  "risk-free", "urgent", "100%", "cash", "winner".
- BANNED stock juice pitch: "real ingredients, real flavour", "no additives",
  "we make functional juice", "premium quality" as opener.

═══════════════════════════════════════════════════════════════
PRODUCT FACTS (use AT MOST ONE per message, pick what fits)
═══════════════════════════════════════════════════════════════

- Ready-to-open / grab-and-go (no prep, no blender) — busy ops.
- 60-day HPP shelf life with cold chain — venues that hate spoilage.
- Medicinal mushrooms (lion's mane, reishi) — wellness / longevity-led venues.
- Strong wholesale margin (~31%) — F&B Directors and GMs who own the P&L.
- Organic, real fruits/veg/roots — premium / natural-positioning venues.

NEVER more than one product fact in one message.

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
  "linkedin_connection": "Short connection note (under 300 chars, no pitch, one observation)",
  "linkedin_opener": "Opening message after connection (under 500 chars, observation + soft ask)",
  "email_subject": "1-5 words, ALL LOWERCASE, no marketing language, hints at line 1 of body",
  "email_opener": "Cold email body — 50-75 words, 3-4 sentences, ONE idea, ONE link max, interest CTA question, signature on its own lines, optional P.S. (HARD CAP 100 words; >150 = reply-rate cliff)",
  "followup_day3": "Day 3 nudge, 80-120 words, NEW angle or NEW number — never 'bumping this up'",
  "followup_day7": "Day 7 nudge, 80-120 words, different pain + different comparable",
  "followup_day14": "Day 14 soft pivot, 60-100 words, low-pressure",
  "reengage": "Week 5+ re-engage — Dean-Jackson 9-word style: one sentence, one question, easy yes/no/not-yet reply"
}}

Reminder: subject MUST be lowercase. email_opener MUST be ≤100 words (target 50-75). The opener line MUST be a specific observation about the prospect, NOT 'I hope you're well'. CTA MUST be a question, NEVER a calendar link.
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


# ── Inbound response drafter ──────────────────────────────────────────────────
# When a prospect reaches out to us first (forwarded email, WhatsApp, IG DM),
# the cold-pitch email_opener is the wrong tone. Use this instead to draft a
# warm, on-point response that addresses their specific intent.

INBOUND_SYSTEM_PROMPT = f"""\
You are drafting a response to an inbound message a prospect just sent to MOM, the Longevity Alchemists.

{BRAND_CONTEXT}

CRITICAL RULES FOR INBOUND RESPONSES:
- They wrote first. Acknowledge their message warmly in the first line.
- Address their specific ask directly. If they want a price list, say you'll send it. If they want a tasting, propose two slots. If they want a partnership, ask one clarifying question.
- DO NOT pitch the product as if cold. They already opted in by writing.
- Keep it short. 60-120 words for the body. Long enough to answer, short enough to not feel like a sales push.
- Match the rep's voice and signature style.
- One clear next step at the end (a question, a proposed slot, or "I'll send X by Y").

PUNCTUATION:
- NEVER use em dashes (—) or en dashes (–). Comma, full stop, or "and".
- NEVER use double hyphens (--).
- Avoid AI tells: "I hope this finds you well", "circle back", "leverage", "synergy".

Respond ONLY with valid JSON in this shape:
{{
  "subject": "string (subject line if email; can be empty for chat replies)",
  "body": "string (the actual reply)"
}}
"""


def generate_inbound_response(
    *,
    venue_name: str,
    intent: str,
    inbound_message: str,
    contact_name: Optional[str],
    rep_id: str,
    venue_type: Optional[str] = None,
) -> dict:
    """Generate a tailored response to an inbound message.

    Returns {'subject': str, 'body': str}. Uses prompt caching on the system
    prompt + brand context.
    """
    rep = get_rep_by_id(rep_id)
    if not rep:
        raise ValueError(f"Rep '{rep_id}' not found in reps.yaml")

    rep_context = _build_rep_context(rep)

    user_content = f"""\
{rep_context}

INBOUND MESSAGE FROM {contact_name or 'the prospect'} at {venue_name}{f' ({venue_type})' if venue_type else ''}:
\"\"\"
{inbound_message}
\"\"\"

THEIR INTENT: {intent}

Draft a warm, direct response. Address their specific ask. Keep it 60-120 words for the body. Output JSON only.
"""

    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=[{
            "type": "text",
            "text": INBOUND_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw)
    return {
        "subject": str(data.get("subject") or "").strip(),
        "body": str(data.get("body") or "").strip(),
    }


# ── Conversation follow-up drafter ────────────────────────────────────────────
# Once a deal is in active conversation, the cold-cadence templates (D3/D7/D14
# stock followups generated up-front) are wrong: they read like spam over the
# top of a real exchange. This generator drafts a fresh, context-aware follow-up
# that references the actual last message we sent and the prospect's intent.

CONVO_FOLLOWUP_SYSTEM_PROMPT = f"""\
You are drafting a follow-up nudge in an ongoing B2B sales conversation for MOM (Longevity Alchemists).

{BRAND_CONTEXT}

CRITICAL RULES FOR CONVERSATION FOLLOW-UPS:
- This is NOT a cold pitch. Both sides have already engaged. Don't reintroduce yourself or re-pitch the brand.
- Reference the previous exchange specifically. Acknowledge their last message and what we proposed.
- Be light, human, low-pressure. Keep it 30-80 words for the body.
- Single ask: a clarifying question, a date suggestion, or a "still interested?" check.
- Match the tone of escalation by `nudge_count`:
    1 = friendly reminder ("just nudging this back up")
    2 = quick check-in ("happy to wait if timing isn't right")
    3 = courteous one more try ("let me know either way and I'll stop chasing")
    4+ = warm-but-direct ("if this isn't the right moment, totally fine to close the loop")
- Sign-off: short. Match how the rep usually signs off in their voice samples.

PUNCTUATION:
- NEVER em dashes (— or –) or double hyphens (--).
- NEVER AI tells: "I hope this finds you well", "circling back", "touching base", "just following up to circle back".
- "Just wanted to..." and "Quick one..." are acceptable openers.

Respond ONLY with valid JSON:
{{
  "subject": "short reply-style subject (e.g. 'Re: Mallorca run + MOM') or empty string for a chat channel",
  "body": "the follow-up message"
}}
"""


def generate_conversation_followup(
    *,
    venue_name: str,
    contact_name: Optional[str],
    intent: Optional[str],
    last_outbound_subject: Optional[str],
    last_outbound_body: Optional[str],
    last_inbound_excerpt: Optional[str],
    nudge_count: int,
    days_since_last_outbound: int,
    rep_id: str,
) -> dict:
    """Draft a context-aware nudge based on the actual conversation history.

    Returns {'subject': str, 'body': str}.
    """
    rep = get_rep_by_id(rep_id)
    if not rep:
        raise ValueError(f"Rep '{rep_id}' not found in reps.yaml")

    rep_context = _build_rep_context(rep)

    user_content = f"""\
{rep_context}

CONVERSATION CONTEXT
Venue: {venue_name}
Contact: {contact_name or 'unknown'}
Their original intent: {intent or 'not recorded'}
Days since we last sent: {days_since_last_outbound}
This is nudge #{nudge_count} in the cadence (3, 7, 14, 21, 28 days, then every 5 weeks).

LAST MESSAGE WE SENT
Subject: {last_outbound_subject or '(no subject on file)'}
Body:
\"\"\"
{last_outbound_body or '(body not on file — keep the nudge generic but warm)'}
\"\"\"

THEIR PRIOR MESSAGE TO US (if any)
\"\"\"
{last_inbound_excerpt or '(none on file)'}
\"\"\"

Draft a follow-up nudge. Reference the prior exchange. 30-80 words. JSON only.
"""

    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system=[{
            "type": "text",
            "text": CONVO_FOLLOWUP_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw)
    return {
        "subject": str(data.get("subject") or "").strip(),
        "body": str(data.get("body") or "").strip(),
    }


# ── EA voice validation pass-through ──────────────────────────────────────────
# Used by the EA drafter (brain/drafter.py). For B2B rep sequences (above)
# voice rules differ per rep, so we don't run the validator on them.

def validate_in_perrys_voice(text: str, *, archetype: str = "default", subject: Optional[str] = None) -> dict:
    """Validate a draft against Perry's voice rules. Returns the ValidationResult dict.

    archetype:
      - "default" for replies, follow-ups, declines, vendor financial.
      - "cold" for cold outreach (extra rules: word count, permission line).
    """
    from brain.voice_validator import validate
    return validate(text, archetype=archetype, subject=subject).to_dict()


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
