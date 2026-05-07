"""Inbound lead onboarding.

Path: extract (brain/inbound_extractor.py) → preview/edit in dashboard → call
onboard_inbound() to land the prospect in HubSpot at "replied" stage with the
original message logged as a note and a response draft queued for review.

Why a separate orchestrator from agents.crm.onboard_prospect?
- Inbound prospects skip the cold-pitch sequence (LinkedIn opener, D3/D7/D14
  follow-ups). They wrote first, so the cadence is wrong.
- Stage starts at "replied" (in conversation), not "prospect". The sequencer
  and auto-send both already skip "replied", so no auto-blast over the top.
- Gatekeeper / parent-group / duplicate checks are advisory but not blocking
  here — Perry already vetted the lead by forwarding it.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from brain.inbound_extractor import ExtractedLead
from config.settings import get_rep_by_id, load_icp
from tools import hubspot_client as hs
from tools.outreach_queue import add_to_queue

log = logging.getLogger(__name__)
ICP = load_icp()


def _domain_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"(?:https?://)?(?:www\.)?([^/]+)", url)
    return m.group(1) if m else None


def onboard_inbound(
    lead: ExtractedLead,
    response_draft: dict,
    *,
    rep_id: str = "perry_patraszewski",
    tier: int = 2,
    auto_send_response: bool = False,
) -> dict:
    """Land an inbound lead in HubSpot.

    Returns a dict with company_id, contact_id, deal_id, stage, queue_added,
    duplicate_of (if a deal already exists for this venue).

    `response_draft` should be {'subject': str, 'body': str} — typically from
    agents.writer.generate_inbound_response. If empty, no draft is queued.

    auto_send_response=True will fire the email immediately via Resend (only
    if AUTO_EMAIL_ENABLED + an email address is known). Default off so Perry
    reviews in the queue first.
    """
    if not lead or not lead.is_lead or not lead.venue_name:
        raise ValueError("Lead is empty or marked is_lead=False — refusing to onboard.")

    rep = get_rep_by_id(rep_id) or {}

    # ── Duplicate check (advisory: log + still onboard) ──
    existing = None
    try:
        existing = hs.find_existing_deal_by_venue(lead.venue_name)
    except Exception as e:
        log.warning("Duplicate lookup failed for %r: %s", lead.venue_name, e)

    if existing:
        deal_id = existing["id"]
        log.info("Inbound for %r matches existing deal %s — moving to 'replied' + logging note",
                 lead.venue_name, deal_id)
        try:
            hs.update_deal_stage(deal_id, "replied")
        except Exception as e:
            log.warning("Could not move existing deal %s to replied: %s", deal_id, e)
        try:
            hs.log_note(
                contact_id=None,
                deal_id=deal_id,
                body=_format_inbound_note(lead),
            )
        except Exception:
            pass
        # Queue response draft against the existing deal so the rep replies.
        queued = _queue_response(rep_id, rep, lead, response_draft, deal_id, existing.get("properties", {}))
        return {
            "company_id": None,
            "contact_id": None,
            "deal_id": deal_id,
            "stage": "replied",
            "queue_added": queued,
            "duplicate_of": deal_id,
        }

    # ── Fresh inbound: create company → contact → deal ──
    domain = _domain_from_url(lead.website)
    company_id = hs.upsert_company(
        name=lead.venue_name,
        website=lead.website,
        domain=domain,
    )

    contact_id: Optional[str] = None
    if lead.contact_name or lead.email or lead.phone:
        first, _, last = (lead.contact_name or "Inbound").partition(" ")
        contact_id = hs.upsert_contact(
            email=lead.email,
            first_name=first or "Inbound",
            last_name=last or "Lead",
            company_id=company_id,
            linkedin_url=lead.linkedin_url,
            instagram_handle=lead.instagram_handle,
            phone=lead.phone,
        )

    revenue = ICP["tiers"][f"tier_{tier}"]["monthly_revenue_eur"]
    rep_tag = f"[{rep_id}]"

    deal_id = hs.create_deal(
        name=f"{lead.venue_name} · MOM {rep_tag}",
        company_id=company_id,
        contact_id=contact_id,
        stage="replied",
        rep_id=rep_id,
        tier=tier,
        venue_type=lead.venue_type or "other",
        revenue_potential_eur=revenue,
        next_followup_date=date.today(),
    )

    # Log the original inbound as a note so future you (or Granola, or the EA)
    # can see what they actually wrote.
    try:
        hs.log_note(
            contact_id=contact_id,
            deal_id=deal_id,
            body=_format_inbound_note(lead),
        )
    except Exception as e:
        log.warning("log_note failed for new deal %s: %s", deal_id, e)

    # Save a stub sequence file so the existing dashboard's email-swap logic
    # picks up the response draft (mirrors agents.crm._save_sequence shape).
    _save_inbound_sequence_stub(deal_id, lead, rep_id, rep, response_draft)

    # Start conversation tracking from "they wrote first" (we owe them a reply).
    try:
        from agents import conversation_tracker as ct
        ct.mark_inbound(deal_id)
    except Exception as e:
        log.warning("Could not start conversation tracking for %s: %s", deal_id, e)

    queued = _queue_response(
        rep_id, rep, lead, response_draft, deal_id,
        deal_props={"dealname": f"{lead.venue_name} · MOM {rep_tag}"},
    )

    # ── Optional immediate send ──
    if auto_send_response and lead.email and response_draft.get("body"):
        try:
            from tools.email_sender import send_outreach_email
            result = send_outreach_email(
                to_email=lead.email,
                subject=response_draft.get("subject") or f"Re: {lead.venue_name}",
                body_text=response_draft["body"],
                from_name=rep.get("name"),
                reply_to=rep.get("email"),
            )
            if result.get("sent"):
                hs.log_note(contact_id, deal_id,
                            f"📧 INBOUND RESPONSE SENT to {lead.email} (id: {result.get('id')})")
        except Exception as e:
            log.warning("Auto-send of inbound response failed: %s", e)

    return {
        "company_id": company_id,
        "contact_id": contact_id,
        "deal_id": deal_id,
        "stage": "replied",
        "queue_added": queued,
        "duplicate_of": None,
    }


def _format_inbound_note(lead: ExtractedLead) -> str:
    parts = [
        "📥 INBOUND — captured via the dashboard",
        f"Intent: {lead.intent or 'unspecified'}",
        f"Confidence: {lead.confidence:.2f}",
    ]
    if lead.contact_name or lead.contact_title:
        parts.append(f"From: {lead.contact_name or '?'} ({lead.contact_title or '?'})")
    if lead.email:
        parts.append(f"Email: {lead.email}")
    if lead.phone:
        parts.append(f"Phone: {lead.phone}")
    if lead.address:
        parts.append(f"Address: {lead.address}")
    parts.append("")
    parts.append("--- ORIGINAL MESSAGE ---")
    parts.append(lead.inbound_message or "(empty)")
    return "\n".join(parts)


def _queue_response(
    rep_id: str,
    rep: dict,
    lead: ExtractedLead,
    response_draft: dict,
    deal_id: str,
    deal_props: dict,
) -> bool:
    body = (response_draft or {}).get("body") or ""
    if not body:
        return False
    add_to_queue(rep_id, {
        "venue_name": lead.venue_name,
        "contact_name": lead.contact_name,
        "contact_title": lead.contact_title,
        "email": lead.email,
        "phone": lead.phone,
        "linkedin_url": lead.linkedin_url,
        "instagram_handle": lead.instagram_handle,
        "address": lead.address,
        "deal_id": deal_id,
        "message_type": f"📥 Inbound response — {lead.intent[:40]}" if lead.intent else "📥 Inbound response",
        "channel": "Email" if lead.email else "LinkedIn",
        "message": body,
        "subject": response_draft.get("subject"),
        "_inbound": True,
    })
    return True


def _save_inbound_sequence_stub(
    deal_id: str,
    lead: ExtractedLead,
    rep_id: str,
    rep: dict,
    response_draft: dict,
) -> None:
    """Write a sequence file so the queue page's email-swap fallback works."""
    seq_path = _ROOT / "data" / "sequences" / f"{deal_id}.json"
    seq_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "deal_id": deal_id,
        "prospect_name": lead.venue_name,
        "contact_name": lead.contact_name,
        "contact_title": lead.contact_title,
        "contact_email": lead.email,
        "linkedin_url": lead.linkedin_url,
        "instagram_handle": lead.instagram_handle,
        "phone": lead.phone,
        "rep_id": rep_id,
        "rep_name": rep.get("name"),
        "rep_email": rep.get("email"),
        "messages": {
            "email_opener": {
                "subject": response_draft.get("subject") or "",
                "body": response_draft.get("body") or "",
                "channel": "Email",
            },
        },
        "source": "inbound",
        "intent": lead.intent,
        "captured_at": datetime.utcnow().isoformat() + "Z",
    }
    with open(seq_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
