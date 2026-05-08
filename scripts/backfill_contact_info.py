"""One-shot backfill: run auto_find_contacts() against every queued prospect
that's missing email/LinkedIn/phone/Instagram, and persist the results into
both the per-day queue files and the canonical sequence files.

Usage:
    python scripts/backfill_contact_info.py            # dry-run preview
    python scripts/backfill_contact_info.py --execute  # actually save
    python scripts/backfill_contact_info.py --execute --rep perry_patraszewski
    python scripts/backfill_contact_info.py --execute --max 10   # cap calls
    python scripts/backfill_contact_info.py --execute --push-hubspot

`--push-hubspot` also patches the deal's primary contact in HubSpot
(email, linkedin_bio, twitterhandle, phone) — same path the dashboard uses.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.contact_finder import auto_find_contacts
from tools.outreach_queue import QUEUE_DIR, update_contact_info


SEQUENCE_DIR = _ROOT / "data" / "sequences"
CONTACT_FIELDS = ("email", "linkedin_url", "phone", "instagram_handle")


def _load_json(path: Path) -> Optional[dict | list]:
    try:
        return json.loads(path.read_text())
    except Exception as e:
        print(f"   ⚠️  Couldn't read {path.name}: {e}")
        return None


def _aggregate_unique_deals(rep_filter: Optional[str]) -> dict[str, dict]:
    """Walk every queue file + sequence file, collect one record per deal_id
    with all known contact fields. Returns {deal_id: {...}}.
    """
    out: dict[str, dict] = {}

    # Queue files first — most up-to-date contact info typically lives here.
    for path in sorted(QUEUE_DIR.glob("*.json")):
        rep_id = path.stem.rsplit("_", 1)[0]
        if rep_filter and rep_id != rep_filter:
            continue
        items = _load_json(path) or []
        for it in items:
            deal_id = it.get("deal_id")
            if not deal_id:
                continue
            rec = out.setdefault(deal_id, {
                "deal_id": deal_id,
                "rep_id": rep_id,
                "venue_name": it.get("venue_name"),
                "contact_name": it.get("contact_name"),
                "address": it.get("address"),
                "email": None,
                "linkedin_url": None,
                "phone": None,
                "instagram_handle": None,
                "website": None,
            })
            for k in CONTACT_FIELDS:
                if not rec.get(k) and it.get(k):
                    rec[k] = it[k]
            if not rec.get("address") and it.get("address"):
                rec["address"] = it["address"]
            if not rec.get("contact_name") and it.get("contact_name"):
                rec["contact_name"] = it["contact_name"]

    # Now sequence files for venues that may not be in any queue.
    if SEQUENCE_DIR.exists():
        for path in sorted(SEQUENCE_DIR.glob("*.json")):
            seq = _load_json(path) or {}
            deal_id = seq.get("deal_id") or path.stem
            if rep_filter and seq.get("rep_id") and seq["rep_id"] != rep_filter:
                continue
            rec = out.setdefault(deal_id, {
                "deal_id": deal_id,
                "rep_id": seq.get("rep_id") or rep_filter or "",
                "venue_name": seq.get("prospect_name"),
                "contact_name": seq.get("contact_name"),
                "address": None,
                "email": seq.get("contact_email"),
                "linkedin_url": seq.get("linkedin_url"),
                "phone": seq.get("phone"),
                "instagram_handle": seq.get("instagram_handle"),
                "website": None,
            })
            if not rec.get("contact_name") and seq.get("contact_name"):
                rec["contact_name"] = seq["contact_name"]
            # Fill blanks from sequence file.
            if not rec.get("email") and seq.get("contact_email"):
                rec["email"] = seq["contact_email"]
            if not rec.get("linkedin_url") and seq.get("linkedin_url"):
                rec["linkedin_url"] = seq["linkedin_url"]
            if not rec.get("phone") and seq.get("phone"):
                rec["phone"] = seq["phone"]
            if not rec.get("instagram_handle") and seq.get("instagram_handle"):
                rec["instagram_handle"] = seq["instagram_handle"]
            if not rec.get("venue_name") and seq.get("prospect_name"):
                rec["venue_name"] = seq["prospect_name"]

    return out


def _missing_fields(rec: dict) -> list[str]:
    return [k for k in CONTACT_FIELDS if not rec.get(k)]


def main(execute: bool, rep_filter: Optional[str], max_calls: Optional[int],
         push_hubspot: bool, sleep_between: float) -> int:
    deals = _aggregate_unique_deals(rep_filter)
    if not deals:
        print("No deals found in queues/ or data/sequences/.")
        return 0

    candidates: list[dict] = []
    skipped_complete = 0
    skipped_no_venue = 0
    for deal_id, rec in deals.items():
        if not rec.get("venue_name"):
            skipped_no_venue += 1
            continue
        missing = _missing_fields(rec)
        if not missing:
            skipped_complete += 1
            continue
        rec["__missing"] = missing
        candidates.append(rec)

    print(f"\nDeals scanned:        {len(deals)}")
    print(f"  Already complete:   {skipped_complete}")
    print(f"  No venue name:      {skipped_no_venue}")
    print(f"  Need backfill:      {len(candidates)}")
    if max_calls and len(candidates) > max_calls:
        print(f"  Cap (--max):        {max_calls} (rest will be skipped this run)")
        candidates = candidates[:max_calls]
    print()

    if not candidates:
        print("Nothing to do.")
        return 0

    if not execute:
        print("DRY-RUN. Sample of what would be searched:")
        for rec in candidates[:10]:
            miss = ", ".join(rec["__missing"])
            print(f"  • {rec['venue_name'][:38]:<38}  rep={rec['rep_id']:<22}  missing: {miss}")
        if len(candidates) > 10:
            print(f"  ... and {len(candidates) - 10} more")
        print("\nRe-run with --execute to apply.")
        return 0

    print(f"Searching with auto_find_contacts (Tavily + scrape) — ~3-5s per call.")
    print()
    counters: dict[str, int] = defaultdict(int)
    failures: list[tuple[str, str]] = []

    for i, rec in enumerate(candidates, 1):
        venue = rec["venue_name"]
        rep_id = rec["rep_id"]
        deal_id = rec["deal_id"]
        miss = set(rec["__missing"])
        prefix = f"  [{i}/{len(candidates)}] {venue[:42]:<42}"
        try:
            found = auto_find_contacts(
                venue,
                rec.get("address"),
                known_website=rec.get("website"),
                contact_name=rec.get("contact_name"),
            )
        except Exception as e:
            failures.append((venue, f"search failed: {e}"))
            print(f"{prefix}  ✗ search failed: {e}")
            continue

        # Only patch the fields the record was missing — don't overwrite known
        # values with potentially-wrong fresh-search data.
        patch: dict = {}
        if "email" in miss and found.email:           patch["email"] = found.email
        if "linkedin_url" in miss and found.linkedin_url: patch["linkedin_url"] = found.linkedin_url
        if "phone" in miss and found.phone:           patch["phone"] = found.phone
        if "instagram_handle" in miss and found.instagram_handle: patch["instagram_handle"] = found.instagram_handle

        if not patch:
            print(f"{prefix}  — nothing new found")
            counters["no_match"] += 1
        else:
            try:
                n = update_contact_info(rep_id, deal_id, patch)
            except Exception as e:
                failures.append((venue, f"persist failed: {e}"))
                print(f"{prefix}  ✗ persist failed: {e}")
                continue

            keys = ", ".join(patch.keys())
            print(f"{prefix}  ✓ filled: {keys} (touched {n} files)")
            counters["filled"] += 1
            for k in patch:
                counters[f"field:{k}"] += 1

            if push_hubspot:
                try:
                    from tools.hubspot_client import push_contact_info_to_deal
                    res = push_contact_info_to_deal(deal_id, patch)
                    if res.get("updated"):
                        print(f"      ↳ HubSpot: {', '.join(res['updated'])}")
                    elif res.get("skipped"):
                        print(f"      ↳ HubSpot skipped: {res['skipped']}")
                except Exception as e:
                    print(f"      ↳ HubSpot push failed: {e}")

        if sleep_between:
            time.sleep(sleep_between)

    print()
    print("=" * 60)
    print(f"Filled:        {counters['filled']}")
    print(f"  emails:      {counters.get('field:email', 0)}")
    print(f"  linkedins:   {counters.get('field:linkedin_url', 0)}")
    print(f"  phones:      {counters.get('field:phone', 0)}")
    print(f"  instagrams:  {counters.get('field:instagram_handle', 0)}")
    print(f"No match:      {counters['no_match']}")
    print(f"Failures:      {len(failures)}")
    for v, e in failures:
        print(f"  - {v}: {e}")
    return 0 if not failures else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true", help="Actually save (default: dry-run)")
    p.add_argument("--rep", default=None, help="Only this rep_id (e.g. perry_patraszewski)")
    p.add_argument("--max", type=int, default=None, help="Cap on number of API calls this run")
    p.add_argument("--push-hubspot", action="store_true", help="Also patch HubSpot contacts")
    p.add_argument("--sleep", type=float, default=0.5, help="Seconds between Tavily calls")
    args = p.parse_args()
    sys.exit(main(
        execute=args.execute,
        rep_filter=args.rep,
        max_calls=args.max,
        push_hubspot=args.push_hubspot,
        sleep_between=args.sleep,
    ))
