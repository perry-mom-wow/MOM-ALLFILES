"""CRM agent: orchestrate HubSpot operations for a prospect."""
from __future__ import annotations

import re
from datetime import date, timedelta

from agents.researcher import ProspectProfile
from agents.writer import OutreachSequence
from config.settings import load_icp
from tools import hubspot_client as hs
from tools.outreach_queue import add_to_queue

ICP = load_icp()

ACTIVE_FOLLOWUP_DAYS = ICP["follow_up_schedule"]["active_days"]  # [3, 7, 14]
REENGAGE_WEEKS = ICP["follow_up_schedule"]["re_engagement_weeks"]  # 5
REENGAGE_MAX_MONTHS = ICP["follow_up_schedule"]["re_engagement_max_months"]  # 12


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"(?:https?://)?(?:www\.)?([^/]+)", url)
    return match.group(1) if match else None


def onboard_prospect(
    profile: ProspectProfile,
    sequence: OutreachSequence,
    rep_id: str,
) -> dict:
    """Create company, contact, deal in HubSpot and queue initial messages."""
    # Company
    company_id = hs.upsert_company(
        name=profile.name,
        website=profile.website,
        domain=_domain_from_url(profile.website),
    )

    # Contact
    contact_parts = (profile.contact_name or "Unknown Contact").split(" ", 1)
    first = contact_parts[0]
    last = contact_parts[1] if len(contact_parts) > 1 else ""
    contact_id = hs.upsert_contact(
        email=profile.email,
        first_name=first,
        last_name=last,
        company_id=company_id,
        linkedin_url=profile.linkedin_url,
        instagram_handle=profile.instagram_handle,
        phone=profile.phone,
    )

    # Revenue potential
    tier_key = f"tier_{profile.tier}"
    revenue = ICP["tiers"][tier_key]["monthly_revenue_eur"]

    # First follow-up date = today + 3 days
    next_followup = date.today() + timedelta(days=ACTIVE_FOLLOWUP_DAYS[0])

    deal_id = hs.create_deal(
        name=f"{profile.name} — mom-wow",
        company_id=company_id,
        contact_id=contact_id,
        stage="prospect",
        rep_id=rep_id,
        tier=profile.tier,
        venue_type=profile.venue_type,
        revenue_potential_eur=revenue,
        next_followup_date=next_followup,
    )

    # Log the initial outreach messages as notes
    initial_msgs = [m for m in sequence.messages if m.message_type in ("linkedin_connection", "linkedin_opener", "email_opener")]
    for msg in initial_msgs:
        note = f"[{msg.message_type.upper()} | {msg.channel}]\n{msg.body}"
        hs.log_note(contact_id, deal_id, note)

    # Queue connection request + opener for the rep
    opener = next((m for m in sequence.messages if m.message_type == "linkedin_connection"), None)
    if opener:
        add_to_queue(rep_id, {
            "venue_name": profile.name,
            "contact_name": profile.contact_name,
            "linkedin_url": profile.linkedin_url,
            "message_type": "LinkedIn Connection Request",
            "channel": "LinkedIn",
            "message": opener.body,
            "deal_id": deal_id,
        })

    opener_msg = next((m for m in sequence.messages if m.message_type == "linkedin_opener"), None)
    if opener_msg:
        add_to_queue(rep_id, {
            "venue_name": profile.name,
            "contact_name": profile.contact_name,
            "linkedin_url": profile.linkedin_url,
            "message_type": "LinkedIn Opening Message",
            "channel": "LinkedIn",
            "message": opener_msg.body,
            "deal_id": deal_id,
        })

    # Move deal to Contacted
    hs.update_deal_stage(deal_id, "contacted")

    return {
        "company_id": company_id,
        "contact_id": contact_id,
        "deal_id": deal_id,
        "next_followup": next_followup.isoformat(),
        "revenue_potential_eur": revenue,
    }


def mark_replied(deal_id: str, contact_id: str, channel: str = "unknown") -> None:
    """
    Mark a deal as replied — from ANY channel (LinkedIn, email, phone, WhatsApp, in person).
    This immediately stops ALL automated follow-ups and re-engagements.
    """
    hs.update_deal_stage(deal_id, "replied")
    hs.log_note(
        contact_id,
        deal_id,
        f"✅ Prospect replied via {channel}.\n"
        f"🛑 ALL automated follow-ups and re-engagement messages stopped.\n"
        f"Human-led conversation now active — do not send any further automated messages.",
    )
    _cancel_queued_messages(deal_id)


def _cancel_queued_messages(deal_id: str) -> None:
    """Remove any pending queue items for this deal so they are never sent."""
    from tools.outreach_queue import load_queue, add_to_queue, clear_queue, QUEUE_DIR
    from datetime import date
    import json
    from pathlib import Path

    for queue_file in QUEUE_DIR.glob("*.json"):
        with open(queue_file) as f:
            items = json.load(f)
        filtered = [i for i in items if i.get("deal_id") != deal_id]
        if len(filtered) < len(items):
            with open(queue_file, "w") as f:
                json.dump(filtered, f, indent=2, default=str)


def mark_tasting_booked(deal_id: str, contact_id: str, tasting_date: date) -> None:
    hs.update_deal_stage(deal_id, "tasting_booked")
    hs.log_note(contact_id, deal_id, f"🎉 Tasting booked for {tasting_date.isoformat()}!")


def mark_won(deal_id: str, contact_id: str) -> None:
    hs.update_deal_stage(deal_id, "won")
    hs.log_note(contact_id, deal_id, "🏆 Deal Won!")


def mark_lost(deal_id: str, contact_id: str, reason: str = "") -> None:
    hs.update_deal_stage(deal_id, "lost")
    hs.log_note(contact_id, deal_id, f"❌ Deal Lost. Reason: {reason}")
