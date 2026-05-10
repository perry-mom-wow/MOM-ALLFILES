"""Audit every saved LinkedIn URL against the verifier and clear the failures.

Background: earlier saves used a loose name-match rule that accepted "anyone
with the right first/last name", which produced false positives like Miguel
Palma (Director Comercial at Herdade da Comporta) being saved as the contact
for Restaurante Via Graça. This script re-validates every saved URL against
the strict rule (both names in slug + venue association via fresh search) and
clears anything that fails.

Usage:
    python scripts/verify_linkedin_urls.py            # dry-run
    python scripts/verify_linkedin_urls.py --execute  # apply (clears bad URLs)
    python scripts/verify_linkedin_urls.py --rep perry_patraszewski

Failed URLs are set to None on every queue file + the deal sequence file. The
dashboard's Auto-find can then refill them under the new rule.
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

from tools.contact_finder import verify_linkedin_url
from tools.outreach_queue import QUEUE_DIR

SEQUENCE_DIR = _ROOT / "data" / "sequences"


def _collect_saved_urls(rep_filter: Optional[str]) -> list[dict]:
    """Walk queue + sequence files, collect every (deal, contact, venue, url) tuple."""
    seen: dict[str, dict] = {}

    for path in sorted(QUEUE_DIR.glob("*.json")):
        rep_id = path.stem.rsplit("_", 1)[0]
        if rep_filter and rep_id != rep_filter:
            continue
        try:
            items = json.loads(path.read_text())
        except Exception:
            continue
        for it in items:
            url = it.get("linkedin_url")
            deal_id = it.get("deal_id")
            if not url or not deal_id:
                continue
            seen.setdefault(deal_id, {
                "deal_id": deal_id,
                "rep_id": rep_id,
                "venue_name": it.get("venue_name"),
                "contact_name": it.get("contact_name"),
                "linkedin_url": url,
            })

    if SEQUENCE_DIR.exists():
        for path in sorted(SEQUENCE_DIR.glob("*.json")):
            try:
                seq = json.loads(path.read_text())
            except Exception:
                continue
            url = seq.get("linkedin_url")
            deal_id = seq.get("deal_id") or path.stem
            if not url:
                continue
            if rep_filter and seq.get("rep_id") and seq["rep_id"] != rep_filter:
                continue
            rec = seen.setdefault(deal_id, {
                "deal_id": deal_id,
                "rep_id": seq.get("rep_id") or rep_filter or "",
                "venue_name": seq.get("prospect_name"),
                "contact_name": seq.get("contact_name"),
                "linkedin_url": url,
            })
            rec.setdefault("contact_name", seq.get("contact_name"))
            rec.setdefault("venue_name", seq.get("prospect_name"))

    return list(seen.values())


def _clear_url(rep_id: str, deal_id: str, url: str) -> int:
    """Set linkedin_url to None on every queue file + sequence file matching."""
    cleared = 0
    for path in QUEUE_DIR.glob(f"{rep_id}_*.json"):
        if path.name.endswith(".bak"):
            continue
        try:
            items = json.loads(path.read_text())
        except Exception:
            continue
        changed = False
        for it in items:
            if it.get("deal_id") == deal_id and (it.get("linkedin_url") or "") == url:
                it["linkedin_url"] = None
                changed = True
        if changed:
            with open(path, "w") as f:
                json.dump(items, f, indent=2, default=str)
            cleared += 1

    seq_path = SEQUENCE_DIR / f"{deal_id}.json"
    if seq_path.exists():
        try:
            seq = json.loads(seq_path.read_text())
            if (seq.get("linkedin_url") or "") == url:
                seq["linkedin_url"] = None
                with open(seq_path, "w") as f:
                    json.dump(seq, f, indent=2, default=str)
                cleared += 1
        except Exception:
            pass
    return cleared


def main(execute: bool, rep_filter: Optional[str], sleep_between: float) -> int:
    saved = _collect_saved_urls(rep_filter)
    if not saved:
        print("No saved LinkedIn URLs found.")
        return 0

    print(f"\nVerifying {len(saved)} saved LinkedIn URL(s) against the strict rule.")
    print("Each verify takes 3-5s (fresh Tavily search).\n")

    counters: dict[str, int] = defaultdict(int)
    failures: list[dict] = []

    for i, rec in enumerate(saved, 1):
        url = rec["linkedin_url"]
        venue = rec.get("venue_name") or "?"
        contact = rec.get("contact_name") or "?"
        prefix = f"  [{i}/{len(saved)}] {venue[:30]:<30} · {contact[:22]:<22}"

        try:
            ok, reason = verify_linkedin_url(
                url, contact_name=rec.get("contact_name"), venue_name=rec.get("venue_name"),
            )
        except Exception as e:
            print(f"{prefix}  ⚠️  verify error: {e}")
            counters["error"] += 1
            continue

        if ok:
            print(f"{prefix}  ✓ {reason}")
            counters["pass"] += 1
        else:
            print(f"{prefix}  ✗ {reason}")
            counters["fail"] += 1
            failures.append({**rec, "reason": reason})

        if sleep_between:
            time.sleep(sleep_between)

    print()
    print("=" * 60)
    print(f"Verified ok:  {counters['pass']}")
    print(f"Failed:       {counters['fail']}")
    print(f"Errors:       {counters['error']}")

    if not failures:
        print("\nNothing to clean up.")
        return 0

    if not execute:
        print("\nDRY-RUN. Re-run with --execute to clear the failed URLs.")
        print("\nFailures:")
        for f in failures:
            print(f"  • {f.get('venue_name')} · {f.get('contact_name')}")
            print(f"      url:    {f['linkedin_url']}")
            print(f"      reason: {f['reason']}")
        return 0

    print(f"\nClearing {len(failures)} failed URL(s)...")
    cleared = 0
    for f in failures:
        n = _clear_url(f["rep_id"], f["deal_id"], f["linkedin_url"])
        if n:
            cleared += n
            print(f"  ✓ {f['venue_name']} · {f['contact_name']}  (cleared in {n} file(s))")
    print(f"\nDone. Cleared in {cleared} file(s) total.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true", help="Actually clear failed URLs (default: dry-run)")
    p.add_argument("--rep", default=None, help="Restrict to one rep_id")
    p.add_argument("--sleep", type=float, default=0.5, help="Seconds between verify calls")
    args = p.parse_args()
    sys.exit(main(execute=args.execute, rep_filter=args.rep, sleep_between=args.sleep))
