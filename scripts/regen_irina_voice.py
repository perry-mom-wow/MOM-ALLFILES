"""Rewrite Irina's pending queue copy in her voice.

After consolidating the book under Irina, her queue holds messages that
were generated for Perry/Vasco (their signatures, their tone). This script
re-drafts every pending item's copy via generate_sequence(profile, 'irina')
and updates both the sequence file and the queue items in place.

On validation failure (cold email rules) it retries twice; on persistent
failure the OLD copy is kept and the deal is reported, so nothing breaks.

Usage:
    python scripts/regen_irina_voice.py --execute [--batch-start 0 --batch-size 8]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.outreach_queue import QUEUE_DIR, load_pending
from brain.voice_validator import validate

SEQUENCE_DIR = _ROOT / "data" / "sequences"


def _profile_from_sequence(seq: dict):
    """Rebuild a ProspectProfile from a sequence file, falling back to prior
    message bodies for research context (same approach as the bulk regen)."""
    from agents.researcher import ProspectProfile
    name = seq.get("prospect_name")
    if not name:
        return None
    description = seq.get("description") or ""
    hook = seq.get("personalisation_hook") or ""
    if not description:
        messages = seq.get("messages") or {}
        em = messages.get("email_opener") or {}
        prior = em.get("previous_body") or em.get("body") or ""
        li = (messages.get("linkedin_opener") or {}).get("body") or ""
        context = prior if len(prior) > len(li) else li
        if context:
            description = f"(Reconstructed from prior research) {context[:1200]}"
            hook = hook or context.split(".")[0][:200].strip()
    if not description and not hook:
        return None
    try:
        tier = int(seq.get("tier") or 2)
    except Exception:
        tier = 2
    return ProspectProfile(
        name=name,
        venue_type=seq.get("venue_type") or "restaurant",
        address=seq.get("address") or "Lisboa, Portugal",
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


def _pick_copy(item: dict, msgs: dict) -> tuple[str | None, str | None]:
    """Choose the right regenerated body (+subject) for a queue item."""
    mtype = (item.get("message_type") or "").lower()
    channel = (item.get("channel") or "").lower()
    em = msgs.get("email_opener") or {}
    conn = msgs.get("linkedin_connection") or {}
    opener = msgs.get("linkedin_opener") or {}
    if channel == "email" or "email" in mtype:
        return em.get("body"), em.get("subject")
    if "connection" in mtype:
        return conn.get("body") or opener.get("body"), None
    return opener.get("body") or conn.get("body"), None


def main(execute: bool, batch_start: int, batch_size: int) -> int:
    pending = load_pending("irina")
    # Unique deals, stable order
    deal_ids: list[str] = []
    for it in pending:
        d = it.get("deal_id")
        if d and d not in deal_ids:
            deal_ids.append(d)
    batch = deal_ids[batch_start:batch_start + batch_size]
    print(f"{len(pending)} pending items, {len(deal_ids)} unique deals; "
          f"batch [{batch_start}:{batch_start + batch_size}] = {len(batch)} deals")
    if not batch:
        return 0
    if not execute:
        for d in batch:
            print(f"  would regen deal {d}")
        return 0

    from agents.writer import generate_sequence

    ok, failed, skipped = 0, 0, 0
    regen_msgs: dict[str, dict] = {}

    for deal_id in batch:
        seq_path = SEQUENCE_DIR / f"{deal_id}.json"
        if not seq_path.exists():
            skipped += 1
            continue
        try:
            seq = json.loads(seq_path.read_text())
        except Exception:
            skipped += 1
            continue
        profile = _profile_from_sequence(seq)
        if not profile:
            skipped += 1
            print(f"  {deal_id}  ⏭ thin data, kept old copy", flush=True)
            continue

        new_msgs = None
        for attempt in range(1, 3):
            try:
                sequence = generate_sequence(profile, "irina")
                msgs = {m.message_type: {"subject": m.subject, "body": m.body,
                                         "channel": m.channel}
                        for m in sequence.messages}
                em = msgs.get("email_opener") or {}
                v = validate(em.get("body") or "", archetype="cold",
                             subject=em.get("subject") or "")
                if v.passed:
                    new_msgs = msgs
                    break
            except Exception as e:
                print(f"  {deal_id}  attempt {attempt} error: {e}", flush=True)
        if not new_msgs:
            failed += 1
            print(f"  {profile.name[:40]:<40}  ✗ kept old copy", flush=True)
            continue

        # Preserve prior copy, update sequence file.
        old_msgs = seq.get("messages") or {}
        for k, vv in new_msgs.items():
            prev = old_msgs.get(k) or {}
            vv["previous_body"] = prev.get("body")
            vv["previous_subject"] = prev.get("subject")
        seq["messages"] = {**old_msgs, **new_msgs}
        with open(seq_path, "w") as f:
            json.dump(seq, f, indent=2, default=str)
        try:
            from state.file_sync import mirror_sequence_file
            mirror_sequence_file(deal_id, seq)
        except Exception:
            pass
        regen_msgs[deal_id] = new_msgs
        ok += 1
        print(f"  {profile.name[:40]:<40}  ✓ regenerated", flush=True)

    # Update queue items in place across irina's files.
    from state.file_sync import mirror_queue_file
    for p in sorted(QUEUE_DIR.glob("irina_*.json")):
        if p.name.endswith(".bak"):
            continue
        try:
            day = date.fromisoformat(p.stem.rpartition("_")[2])
            items = json.loads(p.read_text())
        except Exception:
            continue
        changed = False
        for it in items:
            d = it.get("deal_id")
            if d in regen_msgs:
                body, subject = _pick_copy(it, regen_msgs[d])
                if body:
                    it["message"] = body
                    if subject is not None:
                        it["subject"] = subject
                    changed = True
        if changed:
            with open(p, "w") as f:
                json.dump(items, f, indent=2, default=str)
            mirror_queue_file("irina", day, items)

    print(f"\nBatch done: ok={ok} failed={failed} skipped={skipped}", flush=True)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--batch-start", type=int, default=0)
    ap.add_argument("--batch-size", type=int, default=8)
    a = ap.parse_args()
    sys.exit(main(a.execute, a.batch_start, a.batch_size))
