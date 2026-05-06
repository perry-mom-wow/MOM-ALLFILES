#!/usr/bin/env python3
"""Reassign HubSpot deals from one owner to another.

Common use case: an owner was archived (deleted user account), but legacy deals
still reference the old owner_id and don't show up correctly in dashboards or
get routed properly by automations.

⚠️  SAFETY RAILS
    - Default mode is DRY RUN. Preview first, then re-run with --apply.
    - Every change is appended to logs/owner_reassignments.jsonl for audit.
    - The previous Marcus block was lifted on 2026-05-04 after settlement.
      Marcus's deals are now reassignable like any other owner.

Usage:
    # Dry run — list what WOULD change
    python3 scripts/reassign_owners.py --from 33014009 --to 85686362

    # Apply for real
    python3 scripts/reassign_owners.py --from 33014009 --to 85686362 --apply

    # Filter to specific tiers (deal name suffix or amount-based)
    python3 scripts/reassign_owners.py --from 33014009 --to 85686362 --min-amount 750
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Project root on path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tools.hubspot_client import get_all_deals, get_owners, update_deal_owner

LOG_PATH = ROOT / "logs" / "owner_reassignments.jsonl"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="src", required=True, help="Source owner_id (deals currently owned by this)")
    p.add_argument("--to", dest="dst", required=True, help="Target owner_id (deals will be reassigned to this)")
    p.add_argument("--apply", action="store_true", help="Actually perform the reassignment (default: dry-run)")
    p.add_argument("--min-amount", type=float, default=0.0, help="Only reassign deals with amount >= this value (€/mo)")
    p.add_argument("--max-amount", type=float, default=float("inf"), help="Only reassign deals with amount <= this value (€/mo)")
    args = p.parse_args()

    src, dst = str(args.src), str(args.dst)

    # Resolve names for nicer output
    owners = get_owners()
    src_name = owners.get(src, "(unknown)")
    dst_name = owners.get(dst, "(unknown)")
    if dst not in owners:
        print(f"❌ Target owner {dst} not found in HubSpot owners directory. Aborting.", file=sys.stderr)
        return 2

    print(f"From: {src} ({src_name})")
    print(f"To:   {dst} ({dst_name})")
    print()

    # Fetch and filter
    print("Fetching deals from HubSpot...")
    all_deals = get_all_deals()
    matching = []
    for d in all_deals:
        props = d.get("properties", {}) or {}
        if (props.get("hubspot_owner_id") or "") != src:
            continue
        amount = float(props.get("amount") or 0)
        if amount < args.min_amount or amount > args.max_amount:
            continue
        matching.append(d)

    if not matching:
        print(f"No deals owned by {src} match the filters. Nothing to do.")
        return 0

    print(f"\nFound {len(matching)} deal(s) to reassign:\n")
    print(f"  {'ID':<14}  {'Stage':<22}  {'€/mo':>6}  Name")
    print(f"  {'-'*14}  {'-'*22}  {'-'*6}  {'-'*40}")
    for d in matching:
        props = d.get("properties", {}) or {}
        name = (props.get("dealname") or "")[:60]
        stage = (props.get("dealstage") or "")[:22]
        amount = float(props.get("amount") or 0)
        print(f"  {d.get('id', ''):<14}  {stage:<22}  {amount:>6.0f}  {name}")

    if not args.apply:
        print(f"\n[DRY RUN] No changes made. Re-run with --apply to reassign these {len(matching)} deal(s).")
        return 0

    # Confirm
    print(f"\n⚠️  About to reassign {len(matching)} deal(s) from {src_name} → {dst_name}.")
    confirm = input("Type 'yes' to proceed: ").strip().lower()
    if confirm != "yes":
        print("Aborted by user.")
        return 1

    # Apply
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.utcnow().isoformat() + "Z"
    succeeded = failed = 0
    with open(LOG_PATH, "a") as logf:
        for d in matching:
            deal_id = d.get("id", "")
            try:
                update_deal_owner(deal_id, dst)
                succeeded += 1
                logf.write(json.dumps({
                    "ts": now,
                    "deal_id": deal_id,
                    "deal_name": (d.get("properties", {}) or {}).get("dealname", ""),
                    "from": src,
                    "to": dst,
                    "result": "ok",
                }) + "\n")
                print(f"  ✓ {deal_id}")
            except Exception as e:
                failed += 1
                logf.write(json.dumps({
                    "ts": now,
                    "deal_id": deal_id,
                    "from": src,
                    "to": dst,
                    "result": "error",
                    "error": str(e),
                }) + "\n")
                print(f"  ✗ {deal_id} — {e}", file=sys.stderr)

    print(f"\nDone. {succeeded} reassigned, {failed} failed. Audit log: {LOG_PATH}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
