"""Populate today's Daily Queue from existing sequence files.

Discovery is slow + expensive (API spend on Google Maps + Tavily + Hunter +
Anthropic per prospect). For sales-rep continuity we don't always need fresh
discovery — we have 100+ sequence files on disk for deals that exist in
HubSpot but haven't been touched recently. This script pulls those, queues
their LinkedIn / email openers for today, and mirrors into Postgres.

Filters:
- Skip deals in HubSpot stages 'won', 'lost', 'replied', 'tasting_booked',
  'tasting_done' (those are post-cold; human conversation active).
- Skip deals already in conversation_tracker (those go to Conversation
  Nudges, not Cold Queue).
- Skip deals already queued today.
- Take only deals whose HubSpot tag matches the requested rep_id.

Usage:
    python scripts/seed_queue_from_sequences.py                       # dry-run
    python scripts/seed_queue_from_sequences.py --execute              # apply
    python scripts/seed_queue_from_sequences.py --execute --rep perry_patraszewski
    python scripts/seed_queue_from_sequences.py --execute --max 30
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import hubspot_client as hs
from tools.outreach_queue import load_queue, add_to_queue


SEQUENCE_DIR = _ROOT / "data" / "sequences"
SKIP_STAGES = {"won", "lost", "closedwon", "closedlost", "replied",
               "tasting_booked", "tasting_done"}


def _hubspot_tag(deal: dict) -> Optional[str]:
    name = (deal.get("properties") or {}).get("dealname") or ""
    lower = name.lower()
    for tag in ("perry_patraszewski", "vasco", "irina"):
        if f"[{tag}]" in lower:
            return tag
    return None


def main(execute: bool, only_rep: Optional[str], max_per_rep: int) -> int:
    today = date.today()
    print(f"Seeding queues for {today.isoformat()}...\n")

    # 1. Index HubSpot deals by rep tag (skip terminal stages)
    deals_by_rep: dict[str, list[dict]] = {}
    for d in hs.get_all_deals():
        stage = ((d.get("properties") or {}).get("dealstage") or "").lower()
        if stage in SKIP_STAGES:
            continue
        tag = _hubspot_tag(d)
        if not tag:
            continue
        if only_rep and tag != only_rep:
            continue
        deals_by_rep.setdefault(tag, []).append(d)

    if not deals_by_rep:
        print("No matching HubSpot deals found.")
        return 0

    # 2. Load conversation_tracker so we can skip deals already in conversation
    try:
        from agents import conversation_tracker as ct
        in_conversation = {s.deal_id for s in ct.iter_states()
                           if not s.is_terminal()}
    except Exception as e:
        print(f"WARNING: conversation_tracker unavailable: {e}")
        in_conversation = set()

    plan: dict[str, list[dict]] = {}
    for rep_id, deals in deals_by_rep.items():
        # Skip what's already queued today for this rep
        today_items = load_queue(rep_id, today)
        already_queued = {it.get("deal_id") for it in today_items if it.get("deal_id")}

        queue_items: list[dict] = []
        for d in deals:
            if len(queue_items) >= max_per_rep:
                break
            deal_id = d.get("id") or d.get("hs_object_id") or ""
            if not deal_id:
                continue
            if deal_id in already_queued or deal_id in in_conversation:
                continue
            seq_path = SEQUENCE_DIR / f"{deal_id}.json"
            if not seq_path.exists():
                continue
            try:
                seq = json.loads(seq_path.read_text())
            except Exception:
                continue
            messages = seq.get("messages") or {}
            li = messages.get("linkedin_opener") or messages.get("linkedin_connection") or {}
            em = messages.get("email_opener") or {}
            body = li.get("body") or em.get("body")
            if not body:
                continue
            queue_items.append({
                "venue_name": seq.get("prospect_name"),
                "contact_name": seq.get("contact_name"),
                "contact_title": seq.get("contact_title"),
                "email": seq.get("contact_email"),
                "phone": seq.get("phone"),
                "linkedin_url": seq.get("linkedin_url"),
                "instagram_handle": seq.get("instagram_handle"),
                "address": seq.get("address"),
                "deal_id": deal_id,
                "message_type": "LinkedIn Opener (re-queued)" if li.get("body") else "Email Opener (re-queued)",
                "channel": "LinkedIn" if li.get("body") else "Email",
                "message": body,
                "subject": em.get("subject") if not li.get("body") else None,
            })
        plan[rep_id] = queue_items

    # 3. Report what'd happen
    total = sum(len(v) for v in plan.values())
    print(f"Would queue {total} items today:\n")
    for rep_id, items in plan.items():
        print(f"  {rep_id}: {len(items)} items")
        for it in items[:5]:
            print(f"    • {it['venue_name'][:40]:<40}  {it['channel']}")
        if len(items) > 5:
            print(f"    ... and {len(items) - 5} more")
        print()

    if not execute:
        print("DRY-RUN. Re-run with --execute to write.")
        return 0

    print("Writing queue items...")
    for rep_id, items in plan.items():
        for it in items:
            try:
                add_to_queue(rep_id, it, day=today)
            except Exception as e:
                print(f"  ✗ {rep_id} / {it.get('venue_name')}: {e}")
        print(f"  ✓ {rep_id}: queued {len(items)} items for {today.isoformat()}")

    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true", help="Actually queue (default: dry-run)")
    p.add_argument("--rep", default=None, help="Only this rep_id (perry_patraszewski / irina / vasco)")
    p.add_argument("--max", type=int, default=30, help="Max items per rep (default 30)")
    args = p.parse_args()
    sys.exit(main(execute=args.execute, only_rep=args.rep, max_per_rep=args.max))
