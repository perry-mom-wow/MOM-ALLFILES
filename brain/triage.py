"""Triage: classify a Gmail thread snippet into one of six buckets.

Buckets (per spec §10):
    NEEDS_REPLY  Perry must respond, or the EA should draft on his behalf.
    FYI          Useful context, but no reply needed.
    NOISE        Newsletters, receipts, marketing, 2FA codes, shipping.
    CALENDAR     Meeting requests, reschedules, cancellations.
    INVOICE      Anything with a number, payment, statement, AR/AP.
    INTERNAL     From a MOM/Subrio teammate; route to internal action list.

Also returns a detected language (en/pt) and a tier hint (1, 2, or None) when
the sender matches the Tier 1/2 spreadsheet.

Designed to be cheap: prompt-cached system prompt + small max_tokens + JSON only.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from typing import Literal, Optional

import anthropic

from config.settings import ANTHROPIC_API_KEY

log = logging.getLogger(__name__)

Classification = Literal[
    "NEEDS_REPLY", "FYI", "NOISE", "CALENDAR", "INVOICE", "INTERNAL"
]

_CLASSES: tuple[Classification, ...] = (
    "NEEDS_REPLY", "FYI", "NOISE", "CALENDAR", "INVOICE", "INTERNAL",
)

INTERNAL_DOMAINS: tuple[str, ...] = ("mom-wow.com", "subrio.com")

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


@dataclass
class TriageInput:
    thread_id: str
    subject: str
    sender_email: str
    sender_name: Optional[str]
    snippet: str  # first ~500 chars of the latest message
    is_tier1: bool = False
    is_tier2: bool = False

    def to_prompt(self) -> str:
        tier = "Tier 1" if self.is_tier1 else ("Tier 2" if self.is_tier2 else "unknown")
        return (
            f"FROM: {self.sender_name or ''} <{self.sender_email}>\n"
            f"TIER: {tier}\n"
            f"SUBJECT: {self.subject}\n"
            f"SNIPPET:\n{self.snippet}"
        )


@dataclass
class TriageOutput:
    thread_id: str
    classification: Classification
    language: str  # 'en' | 'pt' | 'other'
    confidence: float
    reason: str
    needs_perry_directly: bool  # True if Tier 1 + NEEDS_REPLY
    suggested_owner: Optional[str] = None  # 'perry' | 'ana' | 'laura' | None

    def to_dict(self) -> dict:
        return asdict(self)


SYSTEM_PROMPT = """You are the inbox triage layer for Perry Patraszewski's executive assistant system.

Classify each Gmail thread into EXACTLY ONE bucket:

- NEEDS_REPLY: someone is waiting on Perry; the message asks for a decision, response, or action from him specifically.
- FYI: informational, useful to know, no response needed (e.g. confirmations, status updates, BCCs).
- NOISE: newsletters, marketing, receipts, shipping notifications, 2FA codes, automated alerts.
- CALENDAR: meeting requests, reschedules, cancellations, calendar invites, tasting/event scheduling.
- INVOICE: invoices, statements, payments, AR/AP, anything financial requiring action or receipt.
- INTERNAL: from a MOM or Subrio teammate (Ana, Laura, Vasco, Facundo, Alexandra, Avi, João, Mum); route to the internal action list rather than treating as external correspondence.

Also detect:
- language: "en", "pt", or "other"
- needs_perry_directly: true ONLY if (Tier 1 sender AND classification is NEEDS_REPLY). Tier 1 contacts reach Perry directly; the EA does not auto-draft for them.
- suggested_owner: who should handle it ("perry", "ana", "laura", or null if unclear)

Calibration:
- Confirmation emails after a reply has already been sent → FYI.
- Receipts and shipping notifications → NOISE.
- "Quick check-in" with a question → NEEDS_REPLY.
- An invoice attached for record-keeping with no action requested → FYI.
- A meeting reschedule that just requires acknowledgement → CALENDAR (not NEEDS_REPLY).

Return ONLY valid JSON. No prose, no preamble.

Schema:
{
  "classification": "NEEDS_REPLY" | "FYI" | "NOISE" | "CALENDAR" | "INVOICE" | "INTERNAL",
  "language": "en" | "pt" | "other",
  "confidence": 0.0-1.0,
  "reason": "one short sentence — what tipped the decision",
  "needs_perry_directly": true | false,
  "suggested_owner": "perry" | "ana" | "laura" | null
}
"""


def classify(thread: TriageInput) -> TriageOutput:
    """Classify a single thread. On any error, fall back to FYI with confidence 0."""
    if _is_internal_sender(thread.sender_email):
        return TriageOutput(
            thread_id=thread.thread_id,
            classification="INTERNAL",
            language=_naive_lang(thread.snippet),
            confidence=0.95,
            reason="Sender is on a MOM/Subrio internal domain.",
            needs_perry_directly=False,
            suggested_owner="perry",
        )

    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY not set; falling back to FYI for thread %s", thread.thread_id)
        return _fallback(thread, "FYI", "No API key configured.")

    try:
        client = _get_client()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": thread.to_prompt()}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)

        classification = data.get("classification")
        if classification not in _CLASSES:
            log.warning("Unknown class %r for thread %s; coercing to FYI", classification, thread.thread_id)
            classification = "FYI"

        return TriageOutput(
            thread_id=thread.thread_id,
            classification=classification,
            language=data.get("language", "en"),
            confidence=float(data.get("confidence", 0.5)),
            reason=str(data.get("reason", "")),
            needs_perry_directly=bool(data.get("needs_perry_directly", False)),
            suggested_owner=data.get("suggested_owner"),
        )
    except Exception as e:
        log.exception("Triage failed for thread %s: %s", thread.thread_id, e)
        return _fallback(thread, "FYI", f"Triage error: {e}")


def classify_batch(threads: list[TriageInput]) -> list[TriageOutput]:
    """Sequential by default. Threads we already know are internal short-circuit."""
    return [classify(t) for t in threads]


def _is_internal_sender(email: str) -> bool:
    if not email:
        return False
    e = email.lower()
    return any(e.endswith("@" + d) for d in INTERNAL_DOMAINS)


def _naive_lang(snippet: str) -> str:
    pt_markers = (" ola ", " olá ", " obrigad", " saudações", " boa tarde", " bom dia ")
    s = (" " + (snippet or "").lower() + " ")
    return "pt" if any(m in s for m in pt_markers) else "en"


def _fallback(thread: TriageInput, classification: Classification, reason: str) -> TriageOutput:
    return TriageOutput(
        thread_id=thread.thread_id,
        classification=classification,
        language=_naive_lang(thread.snippet),
        confidence=0.0,
        reason=reason,
        needs_perry_directly=False,
        suggested_owner=None,
    )
