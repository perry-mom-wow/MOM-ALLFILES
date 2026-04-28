"""Sequencer: daily engine that checks who needs a follow-up and queues messages."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from config.settings import load_icp
from tools import hubspot_client as hs
from tools.outreach_queue import add_to_queue

ICP = load_icp()
ACTIVE_DAYS = ICP["follow_up_schedule"]["active_days"]  # [3, 7, 14]
REENGAGE_WEEKS = ICP["follow_up_schedule"]["re_engagement_weeks"]  # 5
REENGAGE_MAX_MONTHS = ICP["follow_up_schedule"]["re_engagement_max_months"]  # 12
MAX_REENGAGE_CYCLES = (REENGAGE_MAX_MONTHS * 4) // REENGAGE_WEEKS  # ~10 cycles

# Persistent store for sequence state (in production this would be HubSpot custom props)
STATE_DIR = Path(__file__).parent.parent / "data"
STATE_DIR.mkdir(exist_ok=True)
SEQUENCE_STATE_FILE = STATE_DIR / "sequence_state.json"


def _load_state() -> dict:
    if SEQUENCE_STATE_FILE.exists():
        with open(SEQUENCE_STATE_FILE) as f:
            return json.load(f)
    return {}


def _save_state(state: dict) -> None:
    with open(SEQUENCE_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def _get_deal_state(state: dict, deal_id: str) -> dict:
    return state.get(deal_id, {
        "followup_index": 0,    # which active followup we're on (0=Day3, 1=Day7, 2=Day14)
        "active_complete": False,
        "reengage_count": 0,
        "last_contact": None,
    })


def run_daily(today: Optional[date] = None) -> dict:
    """
    Check all deals for due follow-ups. Generate and queue messages.
    Returns a summary dict.
    """
    today = today or date.today()
    state = _load_state()
    due_deals = hs.get_deals_needing_followup(today)

    queued = []
    skipped = []

    for deal in due_deals:
        props = deal.get("properties", {})
        deal_id = deal.get("id") or deal.get("hs_object_id")
        stage = (props.get("dealstage") or "").lower()
        rep_id = props.get("hubspot_owner_id", "marcus")  # fallback

        # CRITICAL: never send follow-ups to anyone who has responded or is post-outreach
        # "replied", "tasting_booked", "won", "lost" all mean a human is in conversation
        if stage in ("won", "lost", "replied", "tasting_booked"):
            skipped.append({"deal_id": deal_id, "reason": f"stage={stage} — human conversation active, no automated messages"})
            continue

        deal_state = _get_deal_state(state, deal_id)
        prospect_name = re.sub(r"\s*[—·]\s*(mom-wow|MOM).*$", "", props.get("dealname", "Unknown")).strip()

        if not deal_state["active_complete"]:
            # Still in active follow-up sequence (Day 3, 7, 14)
            idx = deal_state["followup_index"]
            if idx >= len(ACTIVE_DAYS):
                deal_state["active_complete"] = True
                _move_to_nurture(deal_id, state, deal_state, today, rep_id, prospect_name)
            else:
                _queue_active_followup(deal_id, deal_state, state, today, rep_id, prospect_name, idx, queued)
        else:
            # Nurture: re-engage every 5 weeks for up to 12 months
            count = deal_state["reengage_count"]
            if count >= MAX_REENGAGE_CYCLES:
                skipped.append({"deal_id": deal_id, "reason": "max re-engagements reached"})
                continue

            _queue_reengage(deal_id, deal_state, state, today, rep_id, prospect_name, count, queued)

    _save_state(state)

    return {
        "date": today.isoformat(),
        "deals_checked": len(due_deals),
        "messages_queued": len(queued),
        "queued": queued,
        "skipped": skipped,
    }


def _queue_active_followup(deal_id, deal_state, state, today, rep_id, prospect_name, idx, queued):
    day = ACTIVE_DAYS[idx]
    msg_type_map = {0: "followup_day3", 1: "followup_day7", 2: "followup_day14"}
    msg_type = msg_type_map.get(idx, "followup_day14")

    # Load the saved sequence
    from agents.crm import _load_sequence
    seq = _load_sequence(deal_id) or {}
    msg = (seq.get("messages") or {}).get(msg_type, {})
    message_body = msg.get("body") or f"[{msg_type.upper()} — sequence not found for {prospect_name}]"

    # ── Send email automatically if we have an email + body ──
    contact_email = seq.get("contact_email")
    email_sent = False
    if contact_email and msg.get("body"):
        from tools.email_sender import send_outreach_email
        result = send_outreach_email(
            to_email=contact_email,
            subject=f"Following up — {prospect_name}",
            body_text=msg["body"],
            from_name=seq.get("rep_name"),
            reply_to=seq.get("rep_email"),
        )
        if result.get("sent"):
            email_sent = True
            try:
                hs.log_note(
                    contact_id=None,  # noqa — note logged by deal lookup if needed
                    deal_id=deal_id,
                    body=f"📧 EMAIL SENT (Day {day}) to {contact_email} (id: {result.get('id')})",
                )
            except Exception:
                pass

    # ── Always queue LinkedIn version for the rep too ──
    add_to_queue(rep_id, {
        "venue_name": prospect_name,
        "contact_name": seq.get("contact_name"),
        "linkedin_url": seq.get("linkedin_url"),
        "message_type": f"Follow-up Day {day}{' (email auto-sent ✓)' if email_sent else ''}",
        "channel": "LinkedIn",
        "message": message_body,
        "deal_id": deal_id,
        "day": day,
    }, day=today)

    # Advance state
    deal_state["followup_index"] = idx + 1
    deal_state["last_contact"] = today.isoformat()
    if idx + 1 >= len(ACTIVE_DAYS):
        deal_state["active_complete"] = True
        # Schedule nurture start
        next_date = today + timedelta(weeks=REENGAGE_WEEKS)
        hs.update_deal_followup(deal_id, next_date)
        hs.update_deal_stage(deal_id, "nurture")
    else:
        next_day = ACTIVE_DAYS[idx + 1]
        hs.update_deal_followup(deal_id, today + timedelta(days=next_day - ACTIVE_DAYS[idx]))

    state[deal_id] = deal_state
    queued.append({"deal_id": deal_id, "type": msg_type, "rep": rep_id})


def _move_to_nurture(deal_id, state, deal_state, today, rep_id, prospect_name):
    next_date = today + timedelta(weeks=REENGAGE_WEEKS)
    hs.update_deal_stage(deal_id, "nurture")
    hs.update_deal_followup(deal_id, next_date)
    state[deal_id] = deal_state


def _queue_reengage(deal_id, deal_state, state, today, rep_id, prospect_name, count, queued):
    from agents.researcher import ProspectProfile
    from agents.writer import generate_reengage_message

    # Minimal profile for re-engagement generation
    profile = ProspectProfile(
        name=prospect_name,
        venue_type="unknown",
        address=None,
        website=None,
        phone=None,
        email=None,
        linkedin_url=None,
        instagram_handle=None,
        tier=2,
    )

    try:
        msg = generate_reengage_message(profile, rep_id, count + 1)
        add_to_queue(rep_id, {
            "venue_name": prospect_name,
            "message_type": f"Re-engagement #{count + 1}",
            "channel": msg.channel,
            "message": msg.body,
            "deal_id": deal_id,
        }, day=today)
        queued.append({"deal_id": deal_id, "type": "reengage", "count": count + 1, "rep": rep_id})
    except Exception as e:
        pass

    deal_state["reengage_count"] = count + 1
    deal_state["last_contact"] = today.isoformat()
    next_date = today + timedelta(weeks=REENGAGE_WEEKS)
    hs.update_deal_followup(deal_id, next_date, re_engagement_count=count + 1)
    state[deal_id] = deal_state
