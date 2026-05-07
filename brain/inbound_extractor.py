"""Extract a structured lead profile from a forwarded message or screenshot.

Two entry points share the same prompt + JSON schema:
    extract_from_text(raw_text)       — paste of an email, WhatsApp, SMS, etc.
    extract_from_image(image_bytes, media_type) — screenshot of any of the above.

Both return an ExtractedLead. Confidence is the model's self-rated 0-1 for
how sure it is about the venue identification — surface it in the UI so
Perry knows whether to scrutinise the preview before committing.

Calibration:
- "venue_name" is REQUIRED. If the model can't identify a real business,
  return is_lead=False with a reason — Perry can dismiss without onboarding.
- "intent" is a one-line summary of WHAT THEY WANT (price list, tasting,
  partnership, samples, supply, event collaboration, etc.) so the response
  generator can tailor the reply.
- Generic emails that aren't on the venue's domain (e.g. gmail/outlook) are
  kept since real-world inbound often comes from personal addresses.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, asdict
from typing import Optional

import anthropic

from config.settings import ANTHROPIC_API_KEY

log = logging.getLogger(__name__)

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


@dataclass
class ExtractedLead:
    is_lead: bool
    venue_name: str
    contact_name: Optional[str]
    contact_title: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    linkedin_url: Optional[str]
    instagram_handle: Optional[str]
    website: Optional[str]
    address: Optional[str]
    intent: str  # one-line: what they want
    inbound_message: str  # cleaned version of what they wrote
    confidence: float
    reasoning: str  # why is_lead is true/false
    venue_type: Optional[str] = None  # restaurant / hotel / spa / etc
    # If set, extraction failed for TECHNICAL reasons (no API key, model error,
    # bad JSON). Distinct from is_lead=False, which means the model judged the
    # input not to be a real lead. Lets the dashboard show the right error.
    extraction_error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def empty_failure(cls, reason: str, raw: str = "") -> "ExtractedLead":
        return cls(
            is_lead=False,
            venue_name="",
            contact_name=None,
            contact_title=None,
            email=None,
            phone=None,
            linkedin_url=None,
            instagram_handle=None,
            website=None,
            address=None,
            intent="",
            inbound_message=raw[:500],
            confidence=0.0,
            reasoning=reason,
            venue_type=None,
            extraction_error=reason,
        )


SYSTEM_PROMPT = """You parse inbound B2B sales messages for MOM (Longevity Alchemists), a Portuguese cold-press juice brand selling to restaurants, hotels, beach clubs, spas, and wellness venues.

You will be given a forwarded email, a chat message, or a screenshot of either. Extract the prospect's contact details + their intent so the sales agent can capture them in the CRM.

REQUIRED FIELDS:
- is_lead: true if this looks like a real venue/business reaching out about MOM products, false if it's spam, internal email, personal correspondence, or unclear.
- venue_name: the business name. If only a person's name is shown but they mention "our restaurant/hotel/...", use the business name. If you genuinely cannot identify a business, set is_lead=false.
- intent: one short sentence describing WHAT THEY WANT. Examples: "Asking for the price list", "Wants to book a tasting", "Interested in a partnership for an event", "Wants samples for review", "Wants to stock juices in their café".

OPTIONAL FIELDS (set to null if not present):
- contact_name: the person who wrote the message
- contact_title: their role (CEO, F&B Manager, Owner, etc.)
- email: their email address
- phone: their phone number, kept as written
- linkedin_url: any linkedin.com URL
- instagram_handle: their @handle (without the @)
- website: their venue's website
- address: city, country, or full street address
- venue_type: one of restaurant / hotel / cafe / beach_club / spa / wellness_center / gym / event_company / other

ALSO RETURN:
- inbound_message: the cleaned-up text of what they actually wrote (drop forwarding headers, signatures, quoted prior threads — keep just their fresh message). Max 800 chars.
- confidence: 0.0-1.0, how sure you are about the venue identification.
- reasoning: one short sentence on why is_lead is true/false.

Respond with VALID JSON ONLY. No prose, no preamble, no markdown fences.

Schema:
{
  "is_lead": true | false,
  "venue_name": "string",
  "contact_name": "string or null",
  "contact_title": "string or null",
  "email": "string or null",
  "phone": "string or null",
  "linkedin_url": "string or null",
  "instagram_handle": "string or null",
  "website": "string or null",
  "address": "string or null",
  "venue_type": "string or null",
  "intent": "string",
  "inbound_message": "string",
  "confidence": 0.0,
  "reasoning": "string"
}
"""


def _parse_response(raw: str, fallback_text: str) -> ExtractedLead:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        log.warning("Bad JSON from extractor: %s", e)
        return ExtractedLead.empty_failure(
            f"Could not parse model output as JSON: {e}", raw=fallback_text
        )

    return ExtractedLead(
        is_lead=bool(data.get("is_lead", False)),
        venue_name=str(data.get("venue_name") or "").strip(),
        contact_name=data.get("contact_name") or None,
        contact_title=data.get("contact_title") or None,
        email=(data.get("email") or "").strip().lower() or None,
        phone=data.get("phone") or None,
        linkedin_url=data.get("linkedin_url") or None,
        instagram_handle=(data.get("instagram_handle") or "").lstrip("@") or None,
        website=data.get("website") or None,
        address=data.get("address") or None,
        venue_type=data.get("venue_type") or None,
        intent=str(data.get("intent") or "").strip(),
        inbound_message=str(data.get("inbound_message") or fallback_text)[:1500],
        confidence=float(data.get("confidence") or 0.5),
        reasoning=str(data.get("reasoning") or "").strip(),
    )


def extract_from_text(raw_text: str) -> ExtractedLead:
    if not raw_text or not raw_text.strip():
        return ExtractedLead.empty_failure("Empty input.", raw=raw_text or "")
    if not ANTHROPIC_API_KEY:
        return ExtractedLead.empty_failure("ANTHROPIC_API_KEY not set.", raw=raw_text)

    try:
        response = _get_client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": raw_text[:8000]}],
        )
        return _parse_response(response.content[0].text, fallback_text=raw_text)
    except Exception as e:
        log.exception("extract_from_text failed: %s", e)
        return ExtractedLead.empty_failure(f"Model error: {e}", raw=raw_text)


def extract_from_image(image_bytes: bytes, media_type: str = "image/png") -> ExtractedLead:
    """media_type: 'image/png' | 'image/jpeg' | 'image/webp' | 'image/gif'."""
    if not image_bytes:
        return ExtractedLead.empty_failure("Empty image input.")
    if not ANTHROPIC_API_KEY:
        return ExtractedLead.empty_failure("ANTHROPIC_API_KEY not set.")

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    try:
        response = _get_client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Extract the inbound lead from this screenshot.",
                    },
                ],
            }],
        )
        return _parse_response(response.content[0].text, fallback_text="(screenshot)")
    except Exception as e:
        log.exception("extract_from_image failed: %s", e)
        return ExtractedLead.empty_failure(f"Model error: {e}")
