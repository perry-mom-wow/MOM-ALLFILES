"""Auto-send pipeline for Perry's daily 30-prospect outreach batch.

Two stages, designed to be invoked by GitHub Actions on a cron:

    prepare(today): pick up to DAILY_LIMIT uncontacted prospects owned by Perry,
                    snapshot the cold-email payload to data/auto_send/<date>.json,
                    and email Perry a preview of what will go out tomorrow morning.

    send(today):    read that snapshot, send each cold email via Resend with
                    reply_to=perry@mom-wow.com, advance HubSpot deal stage to
                    "contacted", log an activity note, append to a sent log.

State persists as JSON files committed back to the repo by the GitHub Action,
which gives Perry a window to manually edit/delete entries between prepare and
send by editing the file in the GitHub UI.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import get_rep_by_id
from tools import hubspot_client as hs
from tools.email_sender import send_outreach_email

DAILY_LIMIT = 30
PERRY_REP_ID = "perry_patraszewski"
PERRY_REP_TAG = f"[{PERRY_REP_ID}]"
STATE_DIR = _ROOT / "data" / "auto_send"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def _state_path(day: date) -> Path:
    return STATE_DIR / f"{day.isoformat()}.json"


def _sent_path(day: date) -> Path:
    return STATE_DIR / f"{day.isoformat()}.sent.json"


def _load_sequence(deal_id: str) -> dict:
    seq_path = _ROOT / "data" / "sequences" / f"{deal_id}.json"
    if not seq_path.exists():
        return {}
    with open(seq_path) as f:
        return json.load(f)


def _is_uncontacted(deal: dict) -> bool:
    stage = (deal.get("properties", {}).get("dealstage") or "").lower()
    return stage in ("prospect", "appointmentscheduled", "")


def _is_perrys(deal: dict) -> bool:
    name = (deal.get("properties", {}).get("dealname") or "")
    return PERRY_REP_TAG in name


def _candidates_for_today() -> list[dict]:
    """Return up to DAILY_LIMIT uncontacted, Perry-owned deals with a real email + email_opener."""
    deals = hs.get_all_deals()
    out = []
    for deal in deals:
        if not (_is_perrys(deal) and _is_uncontacted(deal)):
            continue
        deal_id = deal.get("id") or deal.get("hs_object_id")
        seq = _load_sequence(deal_id)
        contact_email = seq.get("contact_email")
        messages = (seq.get("messages") or {})
        opener = messages.get("email_opener") or {}
        body = opener.get("body")
        subject = opener.get("subject")
        if not (contact_email and body and subject):
            continue
        prospect_name = (deal.get("properties", {}).get("dealname") or "").split(PERRY_REP_TAG)[0].strip().rstrip("·—-").strip()
        out.append({
            "deal_id": deal_id,
            "prospect_name": prospect_name or seq.get("prospect_name", "Unknown"),
            "contact_name": seq.get("contact_name"),
            "contact_email": contact_email,
            "subject": subject,
            "body": body,
            "rep_id": PERRY_REP_ID,
        })
        if len(out) >= DAILY_LIMIT:
            break
    return out


def prepare(today: Optional[date] = None) -> dict:
    """Pick today's batch, snapshot to disk, email preview to Perry. Idempotent per day."""
    today = today or date.today()
    state_file = _state_path(today)

    if state_file.exists():
        with open(state_file) as f:
            existing = json.load(f)
        return {"status": "already_prepared", "date": today.isoformat(), "count": len(existing.get("items", []))}

    items = _candidates_for_today()
    payload = {
        "date": today.isoformat(),
        "prepared_at": datetime.utcnow().isoformat() + "Z",
        "items": items,
    }
    with open(state_file, "w") as f:
        json.dump(payload, f, indent=2)

    perry = get_rep_by_id(PERRY_REP_ID) or {}
    preview_html = _render_preview(items, today)
    if items:
        from tools.email_sender import send_report_email
        send_report_email(
            to_emails=[perry.get("email", "perry@mom-wow.com")],
            subject=f"MOM auto-send preview · {len(items)} emails queued for {today.isoformat()}",
            html_body=preview_html,
        )
    return {"status": "prepared", "date": today.isoformat(), "count": len(items)}


def send(today: Optional[date] = None) -> dict:
    """Send each queued email; record outcomes in <date>.sent.json."""
    today = today or date.today()
    state_file = _state_path(today)
    if not state_file.exists():
        return {"status": "no_queue", "date": today.isoformat()}

    with open(state_file) as f:
        payload = json.load(f)
    items = payload.get("items", [])
    if not items:
        return {"status": "empty_queue", "date": today.isoformat()}

    perry = get_rep_by_id(PERRY_REP_ID) or {}
    perry_name = perry.get("name") or "Perry Patraszewski"
    perry_email = perry.get("email") or "perry@mom-wow.com"

    results = []
    for item in items:
        result = send_outreach_email(
            to_email=item["contact_email"],
            subject=item["subject"],
            body_text=item["body"],
            from_email=perry_email,
            from_name=perry_name,
            reply_to=perry_email,
        )
        sent_ok = bool(result.get("sent"))
        if sent_ok:
            try:
                hs.update_deal_stage(item["deal_id"], "contacted")
            except Exception as e:
                result["stage_update_error"] = str(e)
            try:
                hs.log_note(
                    contact_id=None,
                    deal_id=item["deal_id"],
                    body=f"📧 AUTO COLD EMAIL SENT to {item['contact_email']} (resend id: {result.get('id')})",
                )
            except Exception:
                pass
        results.append({
            "deal_id": item["deal_id"],
            "prospect_name": item["prospect_name"],
            "to": item["contact_email"],
            "sent": sent_ok,
            "id": result.get("id"),
            "error": result.get("error"),
            "ts": datetime.utcnow().isoformat() + "Z",
        })

    sent_payload = {
        "date": today.isoformat(),
        "sent_at": datetime.utcnow().isoformat() + "Z",
        "results": results,
    }
    with open(_sent_path(today), "w") as f:
        json.dump(sent_payload, f, indent=2)

    sent_count = sum(1 for r in results if r["sent"])

    summary_html = _render_send_summary(results, today)
    from tools.email_sender import send_report_email
    send_report_email(
        to_emails=[perry_email],
        subject=f"MOM auto-send · {sent_count}/{len(results)} sent on {today.isoformat()}",
        html_body=summary_html,
    )
    return {"status": "sent", "date": today.isoformat(), "sent": sent_count, "total": len(results)}


def _render_preview(items: list[dict], today: date) -> str:
    if not items:
        return f"<p>No uncontacted prospects ready for {today.isoformat()}. Auto-send will skip tomorrow.</p>"
    rows = []
    for i, it in enumerate(items, 1):
        body_html = (it["body"] or "").replace("\n", "<br>")
        rows.append(f"""
<div style="border:1px solid #ddd;border-radius:8px;padding:16px;margin:12px 0;">
  <div style="color:#666;font-size:12px;">#{i} · deal {it['deal_id']}</div>
  <div style="font-weight:600;font-size:16px;margin:4px 0;">{it['prospect_name']}</div>
  <div style="color:#444;font-size:13px;">To: {it['contact_email']}{' · ' + it['contact_name'] if it.get('contact_name') else ''}</div>
  <div style="margin-top:8px;font-size:14px;"><strong>Subject:</strong> {it['subject']}</div>
  <div style="margin-top:8px;background:#f7f7f5;padding:12px;border-radius:6px;font-size:14px;line-height:1.5;">{body_html}</div>
</div>""")
    return f"""
<div style="font-family:system-ui,sans-serif;max-width:780px;margin:0 auto;color:#1a1a1a;">
  <h2>MOM auto-send preview</h2>
  <p>{len(items)} cold emails are queued and will go out at 09:00 Lisbon tomorrow.</p>
  <p style="background:#fff7e6;padding:10px;border-radius:6px;font-size:13px;">
    To halt or edit: open <code>data/auto_send/{today.isoformat()}.json</code> in GitHub and edit/remove items, or disable the
    <code>auto-outreach-send</code> workflow before 08:00 UTC tomorrow.
  </p>
  {''.join(rows)}
</div>"""


def _render_send_summary(results: list[dict], today: date) -> str:
    sent = [r for r in results if r["sent"]]
    failed = [r for r in results if not r["sent"]]
    rows_sent = "".join(f"<li>✓ {r['prospect_name']} → {r['to']}</li>" for r in sent)
    rows_failed = "".join(f"<li>✗ {r['prospect_name']} → {r['to']} <em>({r.get('error')})</em></li>" for r in failed)
    return f"""
<div style="font-family:system-ui,sans-serif;max-width:780px;margin:0 auto;color:#1a1a1a;">
  <h2>MOM auto-send · {today.isoformat()}</h2>
  <p>{len(sent)} sent, {len(failed)} failed.</p>
  <h3 style="color:#0a7d2e;">Sent</h3>
  <ul>{rows_sent or '<li><em>none</em></li>'}</ul>
  <h3 style="color:#a13b00;">Failed</h3>
  <ul>{rows_failed or '<li><em>none</em></li>'}</ul>
</div>"""


def _cli() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("prepare", "send"):
        print("Usage: python -m agents.auto_send {prepare|send}")
        sys.exit(2)
    cmd = sys.argv[1]
    result = prepare() if cmd == "prepare" else send()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _cli()
