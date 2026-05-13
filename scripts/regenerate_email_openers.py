"""Bulk-regenerate the cold email_opener for every prospect under the new
canonical rules (Wiki "📨 Cold Email Construction Rules", 2026-05-12).

Each sequence file at data/sequences/<deal_id>.json has its `email_opener`
re-drafted under the strict prompt + validated under the strict cold rules.
Previous subject + body are preserved as `previous_subject` / `previous_body`
so a single file can be rolled back if Perry doesn't like the new version.

Usage:
    python scripts/regenerate_email_openers.py             # dry-run preview
    python scripts/regenerate_email_openers.py --execute   # save in place
    python scripts/regenerate_email_openers.py --execute --rep perry_patraszewski
    python scripts/regenerate_email_openers.py --execute --max 10
    python scripts/regenerate_email_openers.py --execute --only 502146199755

Behaviour:
  - Skips sequence files that don't have at least prospect_name and a
    description / personalisation hook to ground the regen.
  - Retries up to 3 times if the validator fails (word-count cap is the
    usual culprit). After 3 fails, the old copy is kept and the deal is
    logged in the failures list.
  - Inbound-captured sequences (`source == 'inbound'`) are skipped — those
    have their own response generator and aren't cold.
  - 0.4s sleep between calls so we don't pile-up Anthropic rate limits.
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

from agents.researcher import ProspectProfile
from agents.writer import generate_sequence
from brain.voice_validator import validate

SEQUENCE_DIR = _ROOT / "data" / "sequences"


def _build_profile_from_sequence(seq: dict) -> Optional[ProspectProfile]:
    """Build a ProspectProfile from a sequence file. Most files don't store the
    description / personalisation_hook fields — they only have the previously-
    generated message bodies. We extract context from those instead so the
    regen still gets prospect-specific grounding."""
    name = seq.get("prospect_name")
    if not name:
        return None
    description = seq.get("description") or ""
    hook = seq.get("personalisation_hook") or ""

    # Fallback: lift context out of the previously-generated copy. The old
    # email_opener body usually contained the personalisation hook in the
    # opening sentence and an angle on what the prospect cares about.
    if not description:
        messages = seq.get("messages") or {}
        em = messages.get("email_opener") or {}
        prior_body = em.get("previous_body") or em.get("body") or ""
        li_opener = (messages.get("linkedin_opener") or {}).get("body") or ""
        # Use whichever is longer + more substantive as research context.
        context = prior_body if len(prior_body) > len(li_opener) else li_opener
        if context:
            description = f"(Reconstructed from prior research) {context[:1200]}"
            if not hook:
                # Naive hook: first sentence of the prior body.
                first_sentence = context.split(".")[0][:200].strip()
                if first_sentence:
                    hook = first_sentence

    if not description and not hook:
        return None

    try:
        tier = int(seq.get("tier") or 2)
    except Exception:
        tier = 2
    return ProspectProfile(
        name=name,
        venue_type=seq.get("venue_type") or _infer_venue_type(name),
        address=seq.get("address") or "Lisbon",
        website=seq.get("website") or "",
        phone=seq.get("phone") or "",
        email=seq.get("contact_email") or "",
        linkedin_url=seq.get("linkedin_url") or "",
        instagram_handle=seq.get("instagram_handle") or "",
        tier=tier,
        contact_name=seq.get("contact_name") or "",
        contact_title=seq.get("contact_title") or "",
        description=description,
        personalisation_hook=hook,
        health_wellness_angle=seq.get("health_wellness_angle") or "",
    )


def _infer_venue_type(name: str) -> str:
    n = (name or "").lower()
    if any(k in n for k in ("hotel", "palace", "lodge", "inn", "resort", "ritz")): return "hotel"
    if any(k in n for k in ("spa", "wellness", "longevity")): return "wellness_center"
    if any(k in n for k in ("beach club", "rooftop")): return "beach_club"
    if any(k in n for k in ("cafe", "café", "coffee")): return "cafe"
    if any(k in n for k in ("bar", "lounge")): return "bar"
    if any(k in n for k in ("gym", "studio", "club")): return "gym"
    return "restaurant"


def _regen_with_retry(profile: ProspectProfile, *, max_retries: int = 3) -> tuple[Optional[str], Optional[str], int, list]:
    """Returns (subject, body, attempts_used, last_violations).
    On failure after max_retries, returns (None, None, attempts, violations)."""
    last_violations: list = []
    for attempt in range(1, max_retries + 1):
        try:
            seq = generate_sequence(profile, "perry_patraszewski")
        except Exception as e:
            last_violations = [{"rule": "generation_error", "detail": str(e)}]
            continue
        by_type = {m.message_type: m for m in seq.messages}
        em = by_type.get("email_opener")
        if not em:
            last_violations = [{"rule": "no_email_opener", "detail": "model returned no email_opener field"}]
            continue
        subject = getattr(em, "subject", "") or ""
        body = em.body or ""
        result = validate(body, archetype="cold", subject=subject)
        if result.passed:
            return subject, body, attempt, []
        last_violations = [v.to_dict() for v in result.violations if v.severity == "hard"]
    return None, None, max_retries, last_violations


def main(execute: bool, rep_filter: Optional[str], max_deals: Optional[int],
         only_deal: Optional[str], sleep_between: float) -> int:
    if not SEQUENCE_DIR.exists():
        print("No data/sequences/ directory.")
        return 0

    candidates: list[tuple[Path, dict, ProspectProfile]] = []
    skipped: dict[str, int] = defaultdict(int)

    for path in sorted(SEQUENCE_DIR.glob("*.json")):
        if only_deal and path.stem != only_deal:
            continue
        try:
            seq = json.loads(path.read_text())
        except Exception:
            skipped["bad_json"] += 1
            continue
        if rep_filter and seq.get("rep_id") and seq["rep_id"] != rep_filter:
            skipped["other_rep"] += 1
            continue
        if seq.get("source") == "inbound":
            skipped["inbound"] += 1
            continue
        profile = _build_profile_from_sequence(seq)
        if not profile:
            skipped["thin_data"] += 1
            continue
        candidates.append((path, seq, profile))

    total = len(candidates)
    if max_deals and total > max_deals:
        candidates = candidates[:max_deals]
        print(f"Cap (--max): {max_deals} of {total} candidates.")

    print(f"\nSequence files scanned:  {sum(skipped.values()) + total}")
    print(f"  Will regenerate:       {len(candidates)}")
    for k, v in skipped.items():
        print(f"  Skipped ({k}):         {v}")
    print()

    if not candidates:
        return 0

    if not execute:
        print("DRY-RUN. Sample of what would be regenerated:")
        for path, seq, profile in candidates[:8]:
            print(f"  • {profile.name[:38]:<38}  deal={path.stem}  tier={profile.tier}")
        if len(candidates) > 8:
            print(f"  ... and {len(candidates) - 8} more")
        print("\nRe-run with --execute to apply.")
        return 0

    counters: dict[str, int] = defaultdict(int)
    failures: list[dict] = []
    print(f"Regenerating {len(candidates)} email_openers (3-5s each, ~{len(candidates)*4//60} min total).\n")

    for i, (path, seq, profile) in enumerate(candidates, 1):
        prefix = f"  [{i}/{len(candidates)}] {profile.name[:38]:<38}"
        subject, body, attempts, violations = _regen_with_retry(profile)
        if not body:
            counters["failed"] += 1
            failures.append({
                "deal_id": path.stem, "prospect_name": profile.name,
                "violations": violations,
            })
            v = ", ".join(v.get("rule", "?") for v in violations) or "no body"
            print(f"{prefix}  ✗ FAIL after retries ({v})")
            continue

        wc = len(body.split())
        messages = seq.get("messages") or {}
        old = messages.get("email_opener") or {}
        messages["email_opener"] = {
            "subject": subject,
            "body": body,
            "channel": "Email",
            "previous_subject": old.get("subject"),
            "previous_body": old.get("body"),
            "regenerated_at": "2026-05-12",
        }
        seq["messages"] = messages
        with open(path, "w") as f:
            json.dump(seq, f, indent=2, default=str)

        counters[f"attempts:{attempts}"] += 1
        counters["pass"] += 1
        marker = "✓" if attempts == 1 else f"✓ ({attempts}x)"
        print(f"{prefix}  {marker:<6} {wc}w  subj={subject[:32]!r}")

        if sleep_between:
            time.sleep(sleep_between)

    print()
    print("=" * 70)
    print(f"Regenerated:  {counters['pass']}/{len(candidates)}")
    print(f"  First try:  {counters.get('attempts:1', 0)}")
    print(f"  2 tries:    {counters.get('attempts:2', 0)}")
    print(f"  3 tries:    {counters.get('attempts:3', 0)}")
    print(f"Failed:       {counters['failed']}")
    if failures:
        print("\nFailures (old copy preserved):")
        for f in failures:
            rules = ", ".join(v.get("rule", "?") for v in f["violations"])
            print(f"  • {f['prospect_name']} (deal {f['deal_id']}): {rules}")
    return 0 if not failures else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true", help="Actually save (default: dry-run)")
    p.add_argument("--rep", default=None, help="Only this rep_id")
    p.add_argument("--max", type=int, default=None, help="Cap on number of regens this run")
    p.add_argument("--only", default=None, help="Only this specific deal_id (file stem)")
    p.add_argument("--sleep", type=float, default=0.4, help="Seconds between API calls")
    args = p.parse_args()
    sys.exit(main(
        execute=args.execute,
        rep_filter=args.rep,
        max_deals=args.max,
        only_deal=args.only,
        sleep_between=args.sleep,
    ))
