"""Audit every saved email address against the verifier and clear hard fails.

Use after the LinkedIn audit. Same shape — dry-run by default, --execute
clears placeholder local-parts ('john.doe@anything'), unrelated-domain
addresses, and emails that don't match the known contact name.

Soft flags (freemail mismatches, generic on unverified domain) are surfaced
but NOT cleared automatically.

Usage:
    python scripts/verify_emails.py                  # dry-run
    python scripts/verify_emails.py --execute        # clear hard fails
    python scripts/verify_emails.py --rep perry_patraszewski
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.contact_finder import verify_email_address
from tools.outreach_queue import QUEUE_DIR

SEQUENCE_DIR = _ROOT / "data" / "sequences"


def _collect(rep_filter: Optional[str]) -> list[dict]:
    seen: dict[str, dict] = {}
    for path in sorted(QUEUE_DIR.glob("*.json")):
        rep_id = path.stem.rsplit("_", 1)[0]
        if rep_filter and rep_id != rep_filter:
            continue
        try: items = json.loads(path.read_text())
        except Exception: continue
        for it in items:
            email = (it.get("email") or "").strip().lower()
            deal_id = it.get("deal_id")
            if not email or not deal_id:
                continue
            seen.setdefault(deal_id, {
                "deal_id": deal_id,
                "rep_id": rep_id,
                "venue_name": it.get("venue_name"),
                "contact_name": it.get("contact_name"),
                "email": email,
                "website": None,
            })
    if SEQUENCE_DIR.exists():
        for path in sorted(SEQUENCE_DIR.glob("*.json")):
            try: s = json.loads(path.read_text())
            except Exception: continue
            email = (s.get("contact_email") or "").strip().lower()
            deal_id = s.get("deal_id") or path.stem
            if not email: continue
            if rep_filter and s.get("rep_id") and s["rep_id"] != rep_filter:
                continue
            rec = seen.setdefault(deal_id, {
                "deal_id": deal_id,
                "rep_id": s.get("rep_id") or rep_filter or "",
                "venue_name": s.get("prospect_name"),
                "contact_name": s.get("contact_name"),
                "email": email,
                "website": s.get("website"),
            })
            rec.setdefault("website", s.get("website"))
            rec.setdefault("venue_name", s.get("prospect_name"))
            rec.setdefault("contact_name", s.get("contact_name"))
    return list(seen.values())


def _clear_email(rep_id: str, deal_id: str, email: str) -> int:
    cleared = 0
    for path in QUEUE_DIR.glob(f"{rep_id}_*.json"):
        if path.name.endswith(".bak"): continue
        try: items = json.loads(path.read_text())
        except Exception: continue
        changed = False
        for it in items:
            if it.get("deal_id") == deal_id and (it.get("email") or "").lower() == email.lower():
                it["email"] = None
                changed = True
        if changed:
            with open(path, "w") as f:
                json.dump(items, f, indent=2, default=str)
            cleared += 1
    seq_path = SEQUENCE_DIR / f"{deal_id}.json"
    if seq_path.exists():
        try:
            s = json.loads(seq_path.read_text())
            if (s.get("contact_email") or "").lower() == email.lower():
                s["contact_email"] = None
                with open(seq_path, "w") as f:
                    json.dump(s, f, indent=2, default=str)
                cleared += 1
        except Exception:
            pass
    return cleared


def main(execute: bool, rep_filter: Optional[str]) -> int:
    rows = _collect(rep_filter)
    if not rows:
        print("No saved emails found.")
        return 0

    print(f"\nVerifying {len(rows)} saved email(s).\n")
    counters: dict[str, int] = defaultdict(int)
    hard_fails: list[dict] = []
    soft_flags: list[dict] = []

    for rec in rows:
        ok, severity, reason = verify_email_address(
            rec["email"],
            venue_name=rec.get("venue_name"),
            contact_name=rec.get("contact_name"),
            website=rec.get("website"),
        )
        venue = (rec.get("venue_name") or "?")[:32]
        contact = (rec.get("contact_name") or "—")[:22]
        if not ok and severity == "hard":
            counters["hard"] += 1
            hard_fails.append({**rec, "reason": reason})
            print(f"  ✗ HARD  {venue:<32}  {contact:<22}  {rec['email']}")
            print(f"             {reason}")
        elif severity == "soft":
            counters["soft"] += 1
            soft_flags.append({**rec, "reason": reason})
            print(f"  ⚠ SOFT  {venue:<32}  {contact:<22}  {rec['email']}")
            print(f"             {reason}")
        else:
            counters["ok"] += 1

    print()
    print("=" * 70)
    print(f"Pass:        {counters['ok']}")
    print(f"Soft flags:  {counters['soft']} (kept, surface for review)")
    print(f"Hard fails:  {counters['hard']}")

    if not hard_fails:
        print("\nNo hard fails to clear.")
        return 0

    if not execute:
        print("\nDRY-RUN. Re-run with --execute to clear hard fails.")
        return 0

    print(f"\nClearing {len(hard_fails)} hard-fail email(s)...")
    cleared_total = 0
    for f in hard_fails:
        n = _clear_email(f["rep_id"], f["deal_id"], f["email"])
        cleared_total += n
        if n:
            print(f"  ✓ {f.get('venue_name')}: cleared in {n} file(s)")
    print(f"\nDone. Cleared across {cleared_total} file(s) total.")
    print("\nNext: hit Auto-find on each cleared prospect in the Daily Queue, "
          "or run scripts/backfill_contact_info.py --execute to re-find.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true")
    p.add_argument("--rep", default=None)
    args = p.parse_args()
    sys.exit(main(execute=args.execute, rep_filter=args.rep))
