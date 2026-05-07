"""One-shot migration: reassign every HubSpot deal currently tagged [laura]
to Perry. Renames the deal "Venue · MOM [laura]" → "Venue · MOM [perry_patraszewski]"
and updates the hubspot_owner_id so the sequencer/auto-send pick it up.

Usage:
    python scripts/migrate_laura_to_perry.py            # dry-run: preview only
    python scripts/migrate_laura_to_perry.py --execute  # actually update HubSpot

Skips deals already in 'won' or 'lost' (no point reassigning closed business).
Live deals (replied / tasting_booked / tasting_done) are reassigned but the
sequencer still won't auto-message them — those stages mean a human is in the
loop already.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import hubspot_client as hs

OLD_TAG = "[laura]"
NEW_TAG = "[perry_patraszewski]"
SKIP_STAGES = {"won", "lost", "closedwon", "closedlost"}


def find_perrys_owner_id() -> str | None:
    """Look up the HubSpot owner ID for Perry by matching email."""
    owners = hs.get_owners()  # {owner_id: display_name}
    # We don't have email here; match on display name containing 'Perry'.
    for owner_id, name in owners.items():
        if "perry" in (name or "").lower():
            return owner_id
    return None


def find_laura_deals() -> list[dict]:
    """Return raw HubSpot deal dicts whose name contains [laura]."""
    out = []
    for deal in hs.get_all_deals():
        name = (deal.get("properties") or {}).get("dealname") or ""
        if OLD_TAG in name.lower():
            out.append(deal)
    return out


def rename_deal(deal_id: str, new_name: str) -> None:
    client = hs._get_client()
    client.crm.deals.basic_api.update(
        deal_id=deal_id,
        simple_public_object_input={"properties": {"dealname": new_name}},
    )


def main(execute: bool) -> int:
    deals = find_laura_deals()
    if not deals:
        print("No deals tagged [laura] found. Nothing to do.")
        return 0

    perry_owner_id = find_perrys_owner_id()
    if execute and not perry_owner_id:
        print("WARNING: could not resolve Perry's HubSpot owner ID. Will rename "
              "deal name but leave hubspot_owner_id unchanged.")

    skipped: list[tuple[str, str]] = []
    plan: list[tuple[str, str, str, str]] = []  # (deal_id, old_name, new_name, stage)

    for d in deals:
        deal_id = d.get("id") or d.get("hs_object_id") or ""
        props = d.get("properties") or {}
        old_name = props.get("dealname") or ""
        stage = (props.get("dealstage") or "").lower()
        new_name = re.sub(
            re.escape(OLD_TAG), NEW_TAG, old_name, flags=re.IGNORECASE
        )
        if stage in SKIP_STAGES:
            skipped.append((old_name, f"stage={stage}"))
            continue
        plan.append((deal_id, old_name, new_name, stage))

    print(f"\nFound {len(deals)} deal(s) tagged [laura].")
    print(f"  Will reassign: {len(plan)}")
    print(f"  Will skip:     {len(skipped)} (closed/lost)\n")

    print("=" * 70)
    print(f"{'STAGE':14}  {'OLD NAME':<35}  →  NEW NAME")
    print("=" * 70)
    for _, old, new, stage in plan:
        print(f"{stage:14}  {old[:33]:<35}  →  {new[:33]}")
    if skipped:
        print("\nSkipped:")
        for name, reason in skipped:
            print(f"  - {name[:50]}  ({reason})")
    print("=" * 70)

    if not execute:
        print("\nDRY-RUN. No changes made. Re-run with --execute to apply.")
        return 0

    print(f"\nApplying changes (Perry owner_id={perry_owner_id})...")
    failures: list[tuple[str, str]] = []
    for deal_id, old, new, _ in plan:
        try:
            rename_deal(deal_id, new)
            if perry_owner_id:
                hs.update_deal_owner(deal_id, perry_owner_id)
            print(f"  ✓ {old[:50]}")
        except Exception as e:
            failures.append((old, str(e)))
            print(f"  ✗ {old[:50]}  ({e})")

    print(f"\nDone. Updated {len(plan) - len(failures)}/{len(plan)}.")
    if failures:
        print("Failures:")
        for old, err in failures:
            print(f"  - {old}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true",
                        help="Actually apply changes (default is dry-run)")
    args = parser.parse_args()
    sys.exit(main(execute=args.execute))
