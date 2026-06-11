"""Enrich today's queue items that are missing contact data.

Why: deals created manually in HubSpot (no agent onboarding) have no
researcher data — no contact name, email, LinkedIn, Instagram. Their queue
cards render as just a phone number. This script fills the gaps:

  1. HubSpot primary contact first (authoritative, free).
  2. auto_find_contacts (Tavily web search) for whatever is still missing.
  3. Saves via tools.outreach_queue.update_contact_info → queue files +
     sequence file + Postgres mirror + LinkedIn verification gate.
  4. Pushes back to HubSpot where a contact record exists.

Usage:
    python scripts/enrich_queue_contacts.py --rep irina            # dry-run
    python scripts/enrich_queue_contacts.py --rep irina --execute
    python scripts/enrich_queue_contacts.py --all --execute        # every rep
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import hubspot_client as hs
from tools.outreach_queue import load_pending, update_contact_info
from tools.contact_finder import auto_find_contacts

NEED_FIELDS = ("email", "linkedin_url", "instagram_handle", "contact_name")


def _from_hubspot(deal_id: str) -> dict:
    """Pull contact fields from the deal's primary HubSpot contact, if any."""
    try:
        contact_id = hs.get_primary_contact_id(deal_id)
        if not contact_id:
            return {}
        c = hs._get_client().crm.contacts.basic_api.get_by_id(
            contact_id=contact_id,
            properties=["firstname", "lastname", "email", "phone",
                        "linkedin_bio", "twitterhandle", "jobtitle"],
        )
        p = c.properties or {}
        out = {}
        first = (p.get("firstname") or "").strip()
        last = (p.get("lastname") or "").strip()
        # Skip the "Unknown Contact" placeholder the onboarder creates.
        if first and first.lower() != "unknown":
            out["contact_name"] = f"{first} {last}".strip()
        if p.get("jobtitle"):
            out["contact_title"] = p["jobtitle"]
        if p.get("email"):
            out["email"] = p["email"]
        if p.get("phone"):
            out["phone"] = p["phone"]
        if p.get("linkedin_bio"):
            out["linkedin_url"] = p["linkedin_bio"]
        if p.get("twitterhandle"):
            out["instagram_handle"] = p["twitterhandle"]
        return out
    except Exception:
        return {}


def main(reps: list[str], execute: bool, sleep_between: float) -> int:
    totals = {"checked": 0, "enriched": 0, "nothing_found": 0, "already_complete": 0}

    for rep_id in reps:
        # Pending = today + carryover from the last 14 days, matching what the
        # dashboard shows. update_contact_info patches by deal_id across ALL
        # the rep's queue files, so source date doesn't matter here.
        items = load_pending(rep_id)
        if not items:
            print(f"{rep_id}: no pending queue items.")
            continue
        print(f"\n═══ {rep_id} — {len(items)} pending item(s) ═══")

        for it in items:
            deal_id = it.get("deal_id")
            venue = it.get("venue_name") or "?"
            if not deal_id:
                continue
            missing = [f for f in NEED_FIELDS if not it.get(f)]
            totals["checked"] += 1
            if not missing:
                totals["already_complete"] += 1
                continue

            prefix = f"  {venue[:36]:<36}"
            patch: dict = {}

            # 1. HubSpot contact
            hub = _from_hubspot(deal_id)
            for k, v in hub.items():
                if k in missing or not it.get(k):
                    patch.setdefault(k, v)

            # 2. Web auto-find for what's still missing
            still_missing = [f for f in missing if f not in patch]
            if still_missing and execute:
                try:
                    found = auto_find_contacts(
                        venue, it.get("address"),
                        contact_name=it.get("contact_name") or patch.get("contact_name"),
                    )
                    if "email" in still_missing and found.email:
                        patch["email"] = found.email
                    if "linkedin_url" in still_missing and found.linkedin_url:
                        patch["linkedin_url"] = found.linkedin_url
                    if "instagram_handle" in still_missing and found.instagram_handle:
                        patch["instagram_handle"] = found.instagram_handle
                    if not it.get("phone") and found.phone:
                        patch["phone"] = found.phone
                except Exception as e:
                    print(f"{prefix}  ⚠️ auto-find error: {e}")

            if not patch:
                totals["nothing_found"] += 1
                print(f"{prefix}  — nothing found (missing: {', '.join(missing)})")
                continue

            if not execute:
                print(f"{prefix}  would fill: {', '.join(patch.keys())}")
                totals["enriched"] += 1
                continue

            result = update_contact_info(rep_id, deal_id, patch)
            applied = result.get("applied") or []
            rejected = result.get("rejected") or {}
            totals["enriched"] += 1
            msg = f"{prefix}  ✓ filled: {', '.join(applied)}"
            if rejected:
                msg += f"  (rejected: {', '.join(rejected.keys())})"
            print(msg)

            # 3. Push back to HubSpot where a contact exists
            applied_patch = {k: v for k, v in patch.items() if k in applied}
            if applied_patch:
                try:
                    res = hs.push_contact_info_to_deal(deal_id, applied_patch)
                    if res.get("updated"):
                        print(f"      ↳ HubSpot updated: {', '.join(res['updated'])}")
                    elif res.get("skipped"):
                        print(f"      ↳ HubSpot: {res['skipped']}")
                except Exception as e:
                    print(f"      ↳ HubSpot push failed: {e}")

            if sleep_between:
                time.sleep(sleep_between)

    print()
    print("=" * 60)
    for k, v in totals.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--rep", action="append", default=None,
                   help="Rep id (repeatable). Use --all for every active rep.")
    p.add_argument("--all", action="store_true")
    p.add_argument("--execute", action="store_true", help="Apply (default: dry-run)")
    p.add_argument("--sleep", type=float, default=0.4)
    args = p.parse_args()
    reps = ["perry_patraszewski", "irina", "vasco"] if args.all else (args.rep or ["irina"])
    sys.exit(main(reps, execute=args.execute, sleep_between=args.sleep))
