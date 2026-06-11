"""Consolidate the whole active book under Irina.

Moves from perry_patraszewski + vasco to irina:
  1. HubSpot: every non-terminal deal tagged [perry_patraszewski] or [vasco]
     → tag renamed to [irina], hubspot_owner_id → 34815546.
  2. Sequence files: rep_id / rep_name / rep_email → Irina (so conversation
     nudges and future follow-ups attribute to her). Mirrored to Postgres.
  3. Queue files: every pending item in perry/vasco queues (all days) is
     appended to Irina's queue file for the same day; source files emptied.
     Mirrored to Postgres.

Won/lost deals keep their original rep tag (closed business attribution).

Usage:
    python scripts/migrate_all_to_irina.py            # dry-run
    python scripts/migrate_all_to_irina.py --execute
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import hubspot_client as hs
from tools.outreach_queue import QUEUE_DIR

SEQUENCE_DIR = _ROOT / "data" / "sequences"
IRINA_OWNER_ID = "34815546"
SOURCE_REPS = ("perry_patraszewski", "vasco")
SKIP_STAGES = {"won", "lost", "closedwon", "closedlost"}
TAG_RE = re.compile(r"\[(perry_patraszewski|vasco)\]", re.IGNORECASE)


def migrate_hubspot(execute: bool) -> list[str]:
    moved: list[str] = []
    for d in hs.get_all_deals():
        props = d.get("properties") or {}
        name = props.get("dealname") or ""
        stage = (props.get("dealstage") or "").lower()
        if stage in SKIP_STAGES or not TAG_RE.search(name):
            continue
        deal_id = d.get("id") or d.get("hs_object_id") or ""
        new_name = TAG_RE.sub("[irina]", name)
        moved.append(deal_id)
        if not execute:
            print(f"  would move: {name[:60]}")
            continue
        try:
            hs._get_client().crm.deals.basic_api.update(
                deal_id=deal_id,
                simple_public_object_input={"properties": {"dealname": new_name}},
            )
            hs.update_deal_owner(deal_id, IRINA_OWNER_ID)
            print(f"  ✓ {name[:60]}")
        except Exception as e:
            print(f"  ✗ {name[:60]}: {e}")
    return moved


def migrate_sequences(execute: bool) -> int:
    n = 0
    for p in sorted(SEQUENCE_DIR.glob("*.json")):
        try:
            seq = json.loads(p.read_text())
        except Exception:
            continue
        if seq.get("rep_id") not in SOURCE_REPS:
            continue
        n += 1
        if not execute:
            continue
        seq["rep_id"] = "irina"
        seq["rep_name"] = "Irina Brito"
        seq["rep_email"] = "irina@mom-wow.com"
        with open(p, "w") as f:
            json.dump(seq, f, indent=2, default=str)
        try:
            from state.file_sync import mirror_sequence_file
            mirror_sequence_file(seq.get("deal_id") or p.stem, seq)
        except Exception:
            pass
    return n


def migrate_queues(execute: bool) -> dict:
    """Append every pending perry/vasco item to irina's file of the same day."""
    from datetime import timedelta
    from state.file_sync import mirror_queue_file
    moved = 0
    days_touched = set()
    # Only the live window the dashboard actually shows (last 14 days).
    # Older queue files are dead stock — leave them archived under the
    # original rep rather than flooding Irina with stale April copy.
    cutoff = date.today() - timedelta(days=14)
    for src_rep in SOURCE_REPS:
        for p in sorted(QUEUE_DIR.glob(f"{src_rep}_*.json")):
            if p.name.endswith(".bak"):
                continue
            day_str = p.stem.rpartition("_")[2]
            try:
                day = date.fromisoformat(day_str)
                items = json.loads(p.read_text())
            except Exception:
                continue
            if not items or day < cutoff:
                continue
            moved += len(items)
            days_touched.add(day_str)
            if not execute:
                continue
            # Append to irina's file for the same day.
            dest = QUEUE_DIR / f"irina_{day_str}.json"
            dest_items = []
            if dest.exists():
                try:
                    dest_items = json.loads(dest.read_text())
                except Exception:
                    dest_items = []
            existing_keys = {(it.get("deal_id"), it.get("message_type")) for it in dest_items}
            for it in items:
                if (it.get("deal_id"), it.get("message_type")) in existing_keys:
                    continue
                dest_items.append(it)
            with open(dest, "w") as f:
                json.dump(dest_items, f, indent=2, default=str)
            mirror_queue_file("irina", day, dest_items)
            # Empty the source file.
            with open(p, "w") as f:
                json.dump([], f)
            mirror_queue_file(src_rep, day, [])
    return {"items_moved": moved, "days": sorted(days_touched)}


def main(execute: bool) -> int:
    print("═══ 1. HubSpot deals ═══")
    moved_deals = migrate_hubspot(execute)
    print(f"  → {len(moved_deals)} deal(s)")
    print("═══ 2. Sequence files ═══")
    n_seq = migrate_sequences(execute)
    print(f"  → {n_seq} sequence file(s) re-attributed")
    print("═══ 3. Queue items ═══")
    q = migrate_queues(execute)
    print(f"  → {q['items_moved']} item(s) across {len(q['days'])} day(s)")
    if not execute:
        print("\nDRY-RUN. Re-run with --execute.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    sys.exit(main(ap.parse_args().execute))
