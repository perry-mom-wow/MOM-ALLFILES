"""Daily automation: morning queue emails to reps + evening summary to CEO."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import load_reps, load_icp, REPORT_RECIPIENTS
from config.brand import GREEN, GREEN_DARK, CREAM, WHITE, BLACK, MUSTARD, TERRACOTTA
from tools.email_sender import send_report_email
from tools.outreach_queue import load_queue, load_pending, load_sent
from tools import hubspot_client as hs

ICP = load_icp()


# ── Default daily discovery plan ────────────────────────────────────────────────
# Each weekday morning, agent runs these discovery jobs.
# Tune these per your sales priorities.
DAILY_PLAN = {
    "marcus": [
        {"location": "Lisboa", "types": ["restaurant"], "max_per": 3},
        {"location": "Lisboa", "types": ["beach_club"], "max_per": 2},
    ],
    "laura": [
        {"location": "Lisboa", "types": ["hotel"], "max_per": 3},
        {"location": "Lisboa", "types": ["wellness_center", "spa"], "max_per": 2},
    ],
}


# ── Morning: discover + email each rep their queue ──────────────────────────────

def run_morning_briefing(today: Optional[date] = None) -> dict:
    """Run discovery for each active rep, then email them their daily queue."""
    today = today or date.today()
    print(f"\n☀️  Morning briefing — {today.isoformat()}\n")

    reps = load_reps()
    summary = {"reps_processed": 0, "deals_created": 0, "messages_queued": 0}

    for rep in reps:
        rep_id = rep["id"]
        if not rep.get("active", True):
            continue
        if rep_id not in DAILY_PLAN:
            print(f"   ⏭  {rep['name']}: no DAILY_PLAN entry — skipping")
            continue

        print(f"\n👤 {rep['name']} ({rep_id})")
        deals_before = len(load_queue(rep_id, today))

        # Run each discovery job for this rep
        for job in DAILY_PLAN[rep_id]:
            try:
                from agents.discovery import discover_prospects
                from agents.researcher import research_prospect
                from agents.writer import generate_sequence
                from agents.crm import onboard_prospect, GatekeeperRejection, DuplicateInCRM

                prospects = discover_prospects(
                    location=job["location"],
                    venue_types=job["types"],
                    max_per_type=job["max_per"],
                )
                for raw in prospects:
                    try:
                        profile = research_prospect(raw)
                        sequence = generate_sequence(profile, rep_id)
                        onboard_prospect(profile, sequence, rep_id)
                        summary["deals_created"] += 1
                    except (GatekeeperRejection, DuplicateInCRM):
                        pass  # silently skip — expected
                    except Exception as e:
                        print(f"      ⚠️  {raw.name}: {e}")
            except Exception as e:
                print(f"   ⚠️  Discovery failed for {job}: {e}")

        # Send the queue email
        items = load_queue(rep_id, today)
        new_messages = len(items) - deals_before
        summary["messages_queued"] += new_messages
        summary["reps_processed"] += 1

        if items and rep.get("email"):
            try:
                _send_rep_morning_email(rep, items, today)
                print(f"   📧 Sent queue email to {rep['email']} ({len(items)} messages)")
            except Exception as e:
                print(f"   ⚠️  Email failed for {rep['email']}: {e}")
        else:
            print(f"   ⏭  No queue items for {rep['name']} — no email sent")

    print(f"\n✅ Morning briefing complete. Reps: {summary['reps_processed']} | "
          f"New deals: {summary['deals_created']} | Queued messages: {summary['messages_queued']}\n")
    return summary


def _send_rep_morning_email(rep: dict, items: list[dict], today: date) -> None:
    """Email a single rep their daily queue."""
    name = rep["name"]
    rep_first = name.split(" ")[0]

    msg_blocks = []
    for i, item in enumerate(items, 1):
        venue = item.get("venue_name", "Unknown")
        contact = item.get("contact_name") or "Unknown contact"
        contact_title = item.get("contact_title") or ""
        if contact_title:
            contact = f"{contact} · {contact_title}"
        msg_type = item.get("message_type", "Message")
        channel = item.get("channel", "LinkedIn")
        body = item.get("message", "").replace("\n", "<br>")

        # Build clickable contact channels — LinkedIn + Email + Phone + Instagram
        channels = []
        if item.get("linkedin_url"):
            channels.append(f'<a href="{item["linkedin_url"]}" style="display:inline-block;margin:4px 6px 0 0;padding:6px 12px;background:{GREEN};color:{WHITE};text-decoration:none;border-radius:4px;font-size:12px;">💼 LinkedIn</a>')
        if item.get("email"):
            channels.append(f'<a href="mailto:{item["email"]}" style="display:inline-block;margin:4px 6px 0 0;padding:6px 12px;background:{MUSTARD};color:{WHITE};text-decoration:none;border-radius:4px;font-size:12px;">✉️ {item["email"]}</a>')
        if item.get("phone"):
            channels.append(f'<a href="tel:{item["phone"]}" style="display:inline-block;margin:4px 6px 0 0;padding:6px 12px;background:{TERRACOTTA};color:{WHITE};text-decoration:none;border-radius:4px;font-size:12px;">📞 {item["phone"]}</a>')
        if item.get("instagram_handle"):
            ig = item["instagram_handle"].lstrip("@")
            channels.append(f'<a href="https://instagram.com/{ig}" style="display:inline-block;margin:4px 6px 0 0;padding:6px 12px;background:#E1306C;color:{WHITE};text-decoration:none;border-radius:4px;font-size:12px;">📷 @{ig}</a>')
        channels_html = "<div style='margin-top:10px;'>" + "".join(channels) + "</div>" if channels else ""
        if not channels:
            channels_html = '<div style="margin-top:10px;font-size:12px;color:' + TERRACOTTA + ';font-style:italic;">No contact channels found — visit the venue in person</div>'

        msg_blocks.append(f"""
        <div style="border:1px solid {GREEN}33;border-left:3px solid {GREEN};
                    background:{WHITE};padding:16px;margin-bottom:16px;border-radius:6px;">
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;
                      color:{GREEN_DARK}99;margin-bottom:6px;">
            {i}. {channel} · {msg_type}
          </div>
          <div style="font-size:16px;font-weight:600;color:{GREEN_DARK};margin-bottom:2px;">
            {venue}
          </div>
          <div style="font-size:13px;color:{BLACK}99;margin-bottom:12px;">
            Contact: {contact}
          </div>
          <div style="background:{CREAM};padding:12px;border-radius:4px;
                      font-size:14px;line-height:1.5;color:{BLACK};white-space:pre-wrap;">
            {body}
          </div>
          {channels_html}
        </div>
        """)

    html = f"""
    <html><body style="margin:0;padding:0;background:{CREAM};
                       font-family:-apple-system,'Helvetica Neue',Arial,sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr><td align="center" style="padding:30px 16px;">
          <table width="600" cellpadding="0" cellspacing="0" border="0"
                 style="max-width:600px;background:{WHITE};border-radius:8px;overflow:hidden;">
            <tr><td style="background:{GREEN_DARK};color:{WHITE};padding:24px 28px;">
              <div style="font-size:13px;text-transform:uppercase;letter-spacing:0.12em;
                          color:{CREAM}cc;">MOM · Longevity Alchemists</div>
              <div style="font-size:26px;font-weight:600;margin-top:4px;">
                Good morning, {rep_first}
              </div>
              <div style="font-size:14px;color:{CREAM}cc;margin-top:6px;">
                {today.strftime('%A, %d %B %Y')} · Your daily outreach queue
              </div>
            </td></tr>
            <tr><td style="padding:24px 28px;">
              <p style="font-size:15px;color:{BLACK};margin:0 0 18px;">
                {len(items)} message{'s' if len(items) != 1 else ''} ready to send today.
                Copy each one, paste into LinkedIn, send. Should take ~10 minutes total.
              </p>
              <p style="font-size:13px;color:{BLACK}99;margin:0 0 24px;">
                <strong>If anyone replies on any channel</strong> (LinkedIn, email, phone, in person),
                open the dashboard and hit the "🛑 They Replied" button so the agent stops chasing them.
              </p>
              {''.join(msg_blocks)}
            </td></tr>
            <tr><td style="background:{GREEN_DARK};color:{CREAM}cc;
                           padding:18px 28px;font-size:12px;text-align:center;">
              Sent automatically by your MOM Sales Agent · {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC
            </td></tr>
          </table>
        </td></tr>
      </table>
    </body></html>
    """

    send_report_email(
        to_emails=[rep["email"]],
        subject=f"☀️ Your {today.strftime('%A')} outreach queue · {len(items)} messages",
        html_body=html,
    )


# ── Evening: summary to Perry ───────────────────────────────────────────────────

def _summary_html(today: date, header_title: str, kpi_left: tuple[str, str, str],
                  kpi_right: tuple[str, str, str], rep_rows: str) -> str:
    """Render the evening summary HTML with two KPI tiles and the rep table.

    KPI tuples: (label, big_value_html, sub_caption).
    """
    return f"""
    <html><body style="margin:0;padding:0;background:{CREAM};
                       font-family:-apple-system,'Helvetica Neue',Arial,sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr><td align="center" style="padding:30px 16px;">
          <table width="600" cellpadding="0" cellspacing="0" border="0"
                 style="max-width:600px;background:{WHITE};border-radius:8px;overflow:hidden;">
            <tr><td style="background:{GREEN_DARK};color:{WHITE};padding:24px 28px;">
              <div style="font-size:13px;text-transform:uppercase;letter-spacing:0.12em;color:{CREAM}cc;">
                MOM Sales Agent · {header_title}
              </div>
              <div style="font-size:26px;font-weight:600;margin-top:4px;">
                {today.strftime('%A, %d %B %Y')}
              </div>
            </td></tr>
            <tr><td style="padding:28px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:24px;">
                <tr>
                  <td style="background:{CREAM};padding:18px;border-radius:6px;text-align:center;width:50%;">
                    <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;
                                color:{GREEN_DARK}99;">{kpi_left[0]}</div>
                    <div style="font-size:28px;font-weight:600;color:{GREEN_DARK};margin-top:4px;">
                      {kpi_left[1]}
                    </div>
                    <div style="font-size:13px;color:{BLACK}99;margin-top:4px;">
                      {kpi_left[2]}
                    </div>
                  </td>
                  <td style="width:12px;"></td>
                  <td style="background:{CREAM};padding:18px;border-radius:6px;text-align:center;width:50%;">
                    <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;
                                color:{GREEN_DARK}99;">{kpi_right[0]}</div>
                    <div style="font-size:28px;font-weight:600;color:{GREEN_DARK};margin-top:4px;">
                      {kpi_right[1]}
                    </div>
                    <div style="font-size:13px;color:{BLACK}99;margin-top:4px;">
                      {kpi_right[2]}
                    </div>
                  </td>
                </tr>
              </table>

              <table width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="border:1px solid {GREEN}22;border-radius:6px;">
                {rep_rows}
              </table>
            </td></tr>
            <tr><td style="background:{GREEN_DARK};color:{CREAM}cc;
                           padding:18px 28px;font-size:12px;text-align:center;">
              Generated by your MOM Sales Agent · {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC
            </td></tr>
          </table>
        </td></tr>
      </table>
    </body></html>
    """


def run_evening_summary(today: Optional[date] = None) -> dict:
    """Email each active rep their personal summary, and Perry the full overview."""
    today = today or date.today()
    print(f"\n🌙 Evening summary — {today.isoformat()}\n")

    reps = load_reps()
    rep_stats = []  # list of dicts: {id, name, email, sent, pending}
    total_sent = 0
    total_pending = 0
    for rep in reps:
        if not rep.get("active", True):
            continue
        sent_count = len(load_sent(rep["id"], today))
        pending_count = len(load_pending(rep["id"]))
        rep_stats.append({
            "id": rep["id"],
            "name": rep["name"],
            "email": rep.get("email", ""),
            "sent": sent_count,
            "pending": pending_count,
        })
        total_sent += sent_count
        total_pending += pending_count

    # Company-wide pipeline snapshot
    try:
        deals = hs.get_all_deals()
        total_deals = len(deals)
        total_value = sum(float((d.get("properties") or {}).get("amount") or 0) for d in deals)
    except Exception:
        deals = []
        total_deals = 0
        total_value = 0.0

    # ── 1. Perry's full overview ────────────────────────────────────────────────
    overview_rows = (
        f'<tr><td colspan="3" style="padding:10px 12px;font-family:Georgia,serif;'
        f'color:{GREEN_DARK};font-size:15px;background:{CREAM};">By rep today</td></tr>'
    ) + "".join(
        f'<tr>'
        f'<td style="padding:8px 12px;color:{BLACK};font-size:14px;">{r["name"]}</td>'
        f'<td style="padding:8px 12px;color:{GREEN_DARK};font-size:14px;text-align:right;font-weight:600;">{r["sent"]} sent</td>'
        f'<td style="padding:8px 12px;color:{TERRACOTTA};font-size:14px;text-align:right;font-weight:600;">{r["pending"]} pending</td>'
        f'</tr>' for r in rep_stats
    )
    overview_html = _summary_html(
        today,
        header_title="Daily Summary",
        kpi_left=("Total Pipeline", f"€{total_value:,.0f}/mo", f"across {total_deals} deals"),
        kpi_right=(
            "Sent / Pending Today",
            f'{total_sent} <span style="color:{TERRACOTTA};">/ {total_pending}</span>',
            f"across {len(rep_stats)} reps",
        ),
        rep_rows=overview_rows,
    )
    send_report_email(
        to_emails=REPORT_RECIPIENTS,
        subject=f"🌙 MOM Daily Summary · {today.strftime('%d %b')} · {total_sent} sent / {total_pending} pending · €{total_value:,.0f}/mo pipeline",
        html_body=overview_html,
    )
    print(f"✅ Overview sent to {', '.join(REPORT_RECIPIENTS)}")

    # ── 2. Per-rep personal summaries ───────────────────────────────────────────
    # Identify the CEO (already gets the overview) so we don't double-send.
    ceo_emails = {e.strip().lower() for e in REPORT_RECIPIENTS}
    import re
    sent_rep_count = 0
    for r in rep_stats:
        rep_email = (r.get("email") or "").strip()
        if not rep_email or rep_email.lower() in ceo_emails:
            continue
        # Personal pipeline: deals whose name ends with "[<rep_id>]"
        rep_deals = [
            d for d in deals
            if re.search(rf"\[{re.escape(r['id'])}\]\s*$",
                         (d.get("properties") or {}).get("dealname", ""))
        ]
        rep_value = sum(float((d.get("properties") or {}).get("amount") or 0) for d in rep_deals)

        # Show just-this-rep row, plus a small caption row
        rep_rows = (
            f'<tr>'
            f'<td style="padding:8px 12px;color:{BLACK};font-size:14px;">{r["name"]}</td>'
            f'<td style="padding:8px 12px;color:{GREEN_DARK};font-size:14px;text-align:right;font-weight:600;">{r["sent"]} sent</td>'
            f'<td style="padding:8px 12px;color:{TERRACOTTA};font-size:14px;text-align:right;font-weight:600;">{r["pending"]} pending</td>'
            f'</tr>'
        )
        personal_html = _summary_html(
            today,
            header_title=f"{r['name']} · Daily Summary",
            kpi_left=("Your Pipeline", f"€{rep_value:,.0f}/mo", f"across {len(rep_deals)} deals"),
            kpi_right=(
                "Your Sent / Pending",
                f'{r["sent"]} <span style="color:{TERRACOTTA};">/ {r["pending"]}</span>',
                "today's outreach",
            ),
            rep_rows=rep_rows,
        )
        send_report_email(
            to_emails=[rep_email],
            subject=f"🌙 Your day at MOM · {today.strftime('%d %b')} · {r['sent']} sent / {r['pending']} pending",
            html_body=personal_html,
        )
        sent_rep_count += 1
        print(f"  ✓ Personal summary → {r['name']} <{rep_email}>")

    print(f"✅ Sent personal summaries to {sent_rep_count} rep(s)\n")
    return {
        "total_deals": total_deals,
        "total_value": total_value,
        "sent_today": total_sent,
        "pending": total_pending,
        "reps": [(r["name"], r["sent"], r["pending"]) for r in rep_stats],
    }
