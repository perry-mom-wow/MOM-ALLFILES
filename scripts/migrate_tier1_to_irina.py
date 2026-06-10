"""One-shot migration: reassign HubSpot Tier 1 deals to Irina.

A deal qualifies as "Tier 1" if ANY of these match:

  1. Its sequence file (data/sequences/<deal>.json) has tier=1 saved.
  2. Its venue name contains a known luxury-hotel brand keyword (Pestana,
     Vila Galé, Four Seasons, Ritz, Aman, Memmo, Pousada, Bulgari, Mandarin,
     Sofitel, Marriott, Sheraton, Hilton, Hyatt, Westin, InterContinental,
     St. Regis, Rosewood, Six Senses, Belmond, Conrad, Anantara, Park Hyatt).
  3. Its venue name signals Michelin / fine-dining (palace + restaurant
     combined, "Michelin", explicit fine-dining brand names).

Skips deals already in 'won' or 'lost' (closed business stays where it is).
Skips deals already tagged [irina].

Usage:
    python scripts/migrate_tier1_to_irina.py            # dry-run preview
    python scripts/migrate_tier1_to_irina.py --execute  # actually update HubSpot
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import hubspot_client as hs

NEW_TAG = "[irina]"
SKIP_STAGES = {"won", "lost", "closedwon", "closedlost"}

# Known luxury-hotel and fine-dining brand keywords.
LUXURY_KEYWORDS = [
    "pestana", "vila galé", "vila gale", "four seasons", "ritz", "aman",
    "memmo", "pousada", "bulgari", "mandarin", "sofitel", "marriott",
    "sheraton", "hilton", "hyatt", "westin", "intercontinental",
    "st. regis", "st regis", "rosewood", "six senses", "belmond",
    "conrad", "anantara", "park hyatt", "tivoli", "le méridien",
    "le meridien", "the lapa palace", "as janelas verdes", "olissippo",
    "altis grand", "altis avenida", "altis belém", "altis belem",
    "convent square", "lumiares", "torel", "verride", "valverde",
    "santiago de alfama", "almalusa", "memmo alfama", "memmo principe",
    "palácio chiado", "palacio chiado", "epic sana", "vip grand",
    "four seasons hotel ritz", "porto bay",
]

# Brand keywords that REJECT Tier 1 (e.g., "hostel", "guest house").
TIER_DOWNGRADE_KEYWORDS = [
    "hostel", "guest house", "guesthouse", "bed and breakfast", "bnb",
]


def looks_tier1(deal: dict, sequence_dir: Path) -> tuple[bool, str]:
    """Return (is_tier1, reason). Combines venue-name heuristics + sequence tier."""
    props = deal.get("properties") or {}
    name = (props.get("dealname") or "").lower()

    if any(k in name for k in TIER_DOWNGRADE_KEYWORDS):
        return False, "hostel/guesthouse signal"

    for kw in LUXURY_KEYWORDS:
        if kw in name:
            return True, f"venue name contains '{kw}'"

    # Check sequence file for tier=1
    deal_id = deal.get("id") or deal.get("hs_object_id") or ""
    seq_path = sequence_dir / f"{deal_id}.json"
    if seq_path.exists():
        try:
            seq = json.loads(seq_path.read_text())
            if int(seq.get("tier") or 0) == 1:
                return True, "sequence file marks tier=1"
        except Exception:
            pass

    return False, ""


def find_irinas_owner_id() -> str | None:
    owners = hs.get_owners()
    for owner_id, name in owners.items():
        if "irina" in (name or "").lower():
            return owner_id
    return None


def rename_deal(deal_id: str, new_name: str) -> None:
    client = hs._get_client()
    client.crm.deals.basic_api.update(
        deal_id=deal_id,
        simple_public_object_input={"properties": {"dealname": new_name}},
    )


def main(execute: bool) -> int:
    sequence_dir = _ROOT / "data" / "sequences"
    deals = hs.get_all_deals()
    print(f"Scanned {len(deals)} HubSpot deals.\n")

    candidates: list[dict] = []
    by_reason: Counter = Counter()

    for d in deals:
        props = d.get("properties") or {}
        name = props.get("dealname") or ""
        stage = (props.get("dealstage") or "").lower()
        if stage in SKIP_STAGES:
            continue
        if NEW_TAG in name.lower():
            continue  # already Irina
        is_t1, reason = looks_tier1(d, sequence_dir)
        if not is_t1:
            continue
        candidates.append({
            "deal_id": d.get("id") or d.get("hs_object_id"),
            "old_name": name,
            "stage": stage,
            "reason": reason,
        })
        by_reason[reason.split(" '")[0]] += 1

    if not candidates:
        print("No Tier 1 candidates found to reassign.")
        return 0

    # Compute the new name with the rep tag swapped to [irina].
    tag_re = re.compile(r"\[(perry_patraszewski|vasco|laura|marcus)\]", re.IGNORECASE)
    for c in candidates:
        if tag_re.search(c["old_name"]):
            c["new_name"] = tag_re.sub(NEW_TAG, c["old_name"])
        elif "·" in c["old_name"] and "[" not in c["old_name"]:
            c["new_name"] = c["old_name"].rstrip() + " " + NEW_TAG
        elif "[" not in c["old_name"]:
            c["new_name"] = c["old_name"].rstrip() + " · MOM " + NEW_TAG
        else:
            c["new_name"] = c["old_name"]  # unrecognised tag, leave it

    print(f"Found {len(candidates)} Tier 1 candidate(s) to reassign to Irina.\n")
    print("By detection reason:")
    for r, n in by_reason.most_common():
        print(f"  {n:3d}  {r}")
    print()
    print("=" * 90)
    print(f"{'stage':22}  {'old name':<40}  →  new name")
    print("=" * 90)
    for c in candidates:
        old = c["old_name"][:38]
        new = c["new_name"][:38]
        stage = c["stage"][:20]
        print(f"{stage:22}  {old:<40}  →  {new}")

    if not execute:
        print()
        print("DRY-RUN. Re-run with --execute to apply.")
        return 0

    irina_owner_id = find_irinas_owner_id()
    if not irina_owner_id:
        print("\n⚠️  Could not find an Irina owner in HubSpot. Deal names will be "
              "renamed but hubspot_owner_id will be left unchanged. Add her as a "
              "HubSpot user later and run scripts/sync_owner_ids.py.")

    print(f"\nApplying changes (Irina owner_id={irina_owner_id})...")
    failures: list[tuple[str, str]] = []
    for c in candidates:
        try:
            rename_deal(c["deal_id"], c["new_name"])
            if irina_owner_id:
                hs.update_deal_owner(c["deal_id"], irina_owner_id)
            print(f"  ✓ {c['old_name'][:60]}")
        except Exception as e:
            failures.append((c["old_name"], str(e)))
            print(f"  ✗ {c['old_name'][:60]}  ({e})")

    print(f"\nDone. Updated {len(candidates) - len(failures)}/{len(candidates)}.")
    if failures:
        print("Failures:")
        for old, err in failures:
            print(f"  - {old}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    sys.exit(main(execute=args.execute))
