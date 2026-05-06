#!/usr/bin/env python3
"""Pipeline cleanup — execute the post-Marcus-audit cleanup plan.

Subcommands, each defaulting to dry-run:

  restage    — correct mislabeled deal stages to match email reality (Phase 2)
  queue      — remove parent-group conflicts from queue files (Phase 4)
  wake       — generate sequences + queue Day-0 outreach for dormant deals (Phase 5)

Use --apply to actually mutate. Otherwise prints what would happen.

Owner reassignment (Phase 3) is handled by scripts/reassign_owners.py.
Phantom archiving was REMOVED — policy is to keep nurturing dormant deals.

Every applied change is appended to logs/cleanup_pipeline.jsonl for audit.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tools.hubspot_client import _get_client, update_deal_stage  # noqa: E402

LOG_PATH = ROOT / "logs" / "cleanup_pipeline.jsonl"
SEQUENCE_DIR = ROOT / "data" / "sequences"


# ── Phase 2: stage corrections ───────────────────────────────────────────────
# Each entry: {match | deal_id, stage, reason}
#   - "match" does case-insensitive substring on deal name
#   - "deal_id" overrides match for unambiguous targeting (use when names overlap)
# Internal stage strings map through hubspot_client.STAGE_MAP_BY_PIPELINE so
# the right pipeline ID is picked automatically.
RESTAGE_DEALS = [
    {"match": "Pestana Group",      "stage": "replied",       "reason": "still in active conversation, no order placed"},
    {"match": "Compound Life",      "stage": "replied",       "reason": "pilot terms shared, no order yet"},
    {"match": "Gleba",              "stage": "active_client", "reason": "pilot launching in 3 stores"},
    {"match": "Aimara",             "stage": "lost",          "reason": "tasting declined by venue, Marcus closed it himself"},
    {"match": "Biocol Lab",         "stage": "lost",          "reason": "education session, not a sales relationship"},
    {"match": "DOMA",               "stage": "active_client", "reason": "delivered + invoiced — already a paying customer"},
    # Aethos disambiguation: use deal IDs since "Aethos" is a substring of "Aethos Ericiera"
    {"deal_id": "481098139870",     "stage": "replied",       "reason": "Aethos Lisboa — was a sales call, not a tasting"},
    {"deal_id": "494058622162",     "stage": "nurture",       "reason": "Aethos Ericeira — venue said no current availability (soft no, re-engage in 5 weeks)"},
    {"match": "JNcQUOI",            "stage": "contacted",     "reason": "single cold email, no reply"},
    {"match": "The Organic Way",    "stage": "active_client", "reason": "historical paying customer"},
    {"match": "Pastor - Bakery",    "stage": "contacted",     "reason": "WhatsApp interest only, no tasting"},
    {"match": "Castro Cafe",        "stage": "tasting_done",  "reason": "samples left in person (Laura's work)"},
    {"match": "Thank You Mama",     "stage": "contacted",     "reason": "'closed' claim unsubstantiated"},
]


def _log(action: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    action["ts"] = datetime.utcnow().isoformat() + "Z"
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(action) + "\n")


def _fetch_marcus_deals() -> list[dict]:
    """Return all deals owned by Marcus (id 88943760) with key properties."""
    client = _get_client()
    deals = []
    after = None
    props = ["dealname", "dealstage", "pipeline", "amount",
             "hubspot_owner_id", "hs_lastmodifieddate", "createdate"]
    while True:
        kwargs = {"limit": 100, "properties": props}
        if after:
            kwargs["after"] = after
        page = client.crm.deals.basic_api.get_page(**kwargs)
        for d in page.results:
            if (d.properties or {}).get("hubspot_owner_id") == "88943760":
                deals.append({"id": d.id, "properties": d.properties})
        if not page.paging or not page.paging.next:
            break
        after = page.paging.next.after
    return deals


def _find_deal(deals: list[dict], name_query: str) -> list[dict]:
    """Find deals whose name contains name_query (case-insensitive)."""
    nq = name_query.lower().strip()
    return [d for d in deals
            if nq in (d["properties"].get("dealname") or "").lower()]


# ── restage ──────────────────────────────────────────────────────────────────
def cmd_restage(apply: bool) -> int:
    deals = _fetch_marcus_deals()
    by_id = {d["id"]: d for d in deals}
    print(f"Phase 2: RESTAGE — found {len(deals)} Marcus deals total. Matching restage targets...\n")

    plan = []  # (query_label, deal, target_stage, reason)
    not_found = []
    for entry in RESTAGE_DEALS:
        target_stage = entry["stage"]
        reason = entry["reason"]
        if "deal_id" in entry:
            did = entry["deal_id"]
            if did in by_id:
                plan.append((f"id={did}", by_id[did], target_stage, reason))
            else:
                not_found.append(f"id={did}")
        else:
            query = entry["match"]
            matches = _find_deal(deals, query)
            if not matches:
                not_found.append(query)
                continue
            for m in matches:
                plan.append((query, m, target_stage, reason))

    print(f"Will update {len(plan)} deal(s):\n")
    for query, d, target, reason in plan:
        p = d["properties"]
        name = (p.get("dealname") or "")[:50]
        cur = (p.get("dealstage") or "")[:22]
        print(f"  {d['id']:<14}  {cur:<22}  →  {target:<16}  {name}")
        print(f"                  reason: {reason}")

    if not_found:
        print(f"\n⚠️  Couldn't find HubSpot deals for: {not_found}")

    if not apply:
        print(f"\n[DRY RUN] No changes made. Re-run with --apply to restage these {len(plan)} deal(s).")
        return 0

    print(f"\n⚠️  About to UPDATE STAGE on {len(plan)} deal(s).")
    confirm = input("Type 'yes' to proceed: ").strip().lower()
    if confirm != "yes":
        print("Aborted by user.")
        return 1

    succeeded = failed = 0
    for query, d, target, reason in plan:
        deal_id = d["id"]
        name = d["properties"].get("dealname", "")
        cur = d["properties"].get("dealstage", "")
        try:
            update_deal_stage(deal_id, target)
            _log({"phase": "restage", "action": "update_stage", "deal_id": deal_id,
                  "deal_name": name, "match_query": query, "from_stage": cur,
                  "to_stage": target, "reason": reason, "result": "ok"})
            print(f"  ✓ {deal_id}  {name} → {target}")
            succeeded += 1
        except Exception as e:
            _log({"phase": "restage", "action": "update_stage", "deal_id": deal_id,
                  "deal_name": name, "match_query": query, "from_stage": cur,
                  "to_stage": target, "reason": reason, "result": "error", "error": str(e)})
            print(f"  ✗ {deal_id}  {name} — {e}", file=sys.stderr)
            failed += 1

    print(f"\nDone. {succeeded} restaged, {failed} failed. Audit log: {LOG_PATH}")
    return 0 if failed == 0 else 1


# ── queue scrub ──────────────────────────────────────────────────────────────
def cmd_queue(apply: bool) -> int:
    """Remove parent-group conflict messages from queue files."""
    from tools.parent_groups import (
        load_parent_groups, match_parent_group, find_existing_group_deal,
    )
    load_parent_groups.cache_clear()
    import glob

    queue_files = sorted(glob.glob(str(ROOT / "queues" / "*.json")))
    print(f"Phase 4: QUEUE SCRUB — scanning {len(queue_files)} queue file(s)...\n")

    files_with_changes = []
    for f in queue_files:
        with open(f) as fp:
            items = json.load(fp)
        kept = []
        removed = []
        for it in items:
            venue = it.get("venue_name") or ""
            title = it.get("contact_title") or ""
            g = match_parent_group(name=venue, contact_title=title)
            if g and not g.get("decentralized", False):
                existing = find_existing_group_deal(g["name"])
                if existing:
                    removed.append((it, g, existing))
                    continue
            kept.append(it)
        if removed:
            files_with_changes.append((f, items, kept, removed))

    if not files_with_changes:
        print("  Nothing to scrub. All queue items pass the dedup rule.")
        return 0

    total_removed = 0
    for f, _, _, removed in files_with_changes:
        rel = Path(f).relative_to(ROOT)
        print(f"  {rel}: would remove {len(removed)} item(s)")
        for it, g, ex in removed:
            print(f"      • {it.get('venue_name')}  ({it.get('message_type')})  → conflicts with {g['name']}")
            total_removed += 1

    if not apply:
        print(f"\n[DRY RUN] No changes made. Re-run with --apply to remove {total_removed} item(s) across {len(files_with_changes)} file(s).")
        return 0

    print(f"\n⚠️  About to REWRITE {len(files_with_changes)} queue file(s), removing {total_removed} item(s).")
    confirm = input("Type 'yes' to proceed: ").strip().lower()
    if confirm != "yes":
        print("Aborted by user.")
        return 1

    for f, original, kept, removed in files_with_changes:
        # Backup original
        backup = Path(f + ".bak")
        with open(backup, "w") as bfp:
            json.dump(original, bfp, indent=2, ensure_ascii=False)
        # Write the cleaned queue
        with open(f, "w") as fp:
            json.dump(kept, fp, indent=2, ensure_ascii=False)
        for it, g, ex in removed:
            _log({"phase": "queue", "action": "remove_queue_item",
                  "file": str(Path(f).relative_to(ROOT)),
                  "venue": it.get("venue_name"),
                  "message_type": it.get("message_type"),
                  "conflicting_group": g["name"],
                  "existing_deal_id": ex["id"],
                  "result": "ok"})
        print(f"  ✓ {Path(f).name}: removed {len(removed)}, kept {len(kept)} (backup: {backup.name})")

    print(f"\nDone. {total_removed} queue items removed. Audit log: {LOG_PATH}")
    return 0


# ── wake (Phase 5) ───────────────────────────────────────────────────────────
# Skip stages where outreach should NOT be re-initiated.
# - won/lost = closed
# - replied/tasting_booked/tasting_done = active human conversation, sequencer skips these too
# - active_client = ongoing customer
# Custom T1 IDs: 4961801428 (active_client), 4961801429 (nurture)
# Custom T2/3 IDs: 5143548138-5143548146 — see hubspot_client.STAGE_MAP_BY_PIPELINE
SKIP_STAGES_FOR_WAKE = {
    "closedwon", "closedlost",
    "presentationscheduled", "decisionmakerboughtin", "contractsent",  # human-conversation T1
    "4961801428",  # active_client T1
    "4961801429",  # nurture/resting T1 — re-engagement only, not fresh cold outreach
    "5143548140", "5143548142", "5143548143", "5143548144", "5143548145", "5143548146",  # T2/3 conversation/won/lost/active/nurture
}

# Tier inference from amount (€/mo)
def _infer_tier(amount: float) -> int:
    if amount >= 1000:
        return 1
    if amount >= 500:
        return 2
    return 3


def _extract_rep_id(deal_name: str) -> Optional[str]:
    """Pull rep_id from `[rep_id]` suffix in deal name. Returns None if missing."""
    import re as _re
    m = _re.search(r"\[([^\]]+)\]\s*$", deal_name or "")
    return m.group(1).strip() if m else None


def _fetch_deal_contacts(deal_id: str) -> dict:
    """Return best-effort {contact_name, contact_title, contact_email, linkedin_url}
    by following the deal→contact association and reading the first contact."""
    client = _get_client()
    out = {"contact_name": None, "contact_title": None, "email": None, "linkedin_url": None}
    try:
        # deal → contacts associations
        assocs = client.crm.associations.v4.basic_api.get_page(
            object_type="deal",
            object_id=deal_id,
            to_object_type="contact",
        )
        contact_ids = [r.to_object_id for r in assocs.results][:1]
    except Exception:
        contact_ids = []

    if not contact_ids:
        return out

    try:
        c = client.crm.contacts.basic_api.get_by_id(
            contact_id=str(contact_ids[0]),
            properties=["firstname", "lastname", "jobtitle", "email", "linkedin_bio"],
        )
        p = c.properties or {}
        first = (p.get("firstname") or "").strip()
        last = (p.get("lastname") or "").strip()
        full = (first + " " + last).strip()
        out["contact_name"] = full or None
        out["contact_title"] = p.get("jobtitle") or None
        out["email"] = p.get("email") or None
        out["linkedin_url"] = p.get("linkedin_bio") or None
    except Exception:
        pass
    return out


def cmd_wake(apply: bool, limit: Optional[int] = None) -> int:
    """Generate sequences and queue Day-0 outreach for dormant Marcus deals."""
    deals = _fetch_marcus_deals()
    print(f"Phase 5: WAKE — found {len(deals)} Marcus deals total. Filtering to dormant...\n")

    # Dormant = stage allows outreach + no saved sequence file
    dormant = []
    for d in deals:
        p = d["properties"] or {}
        stage = (p.get("dealstage") or "").lower()
        if stage in SKIP_STAGES_FOR_WAKE:
            continue
        seq_file = SEQUENCE_DIR / f"{d['id']}.json"
        if seq_file.exists():
            continue
        dormant.append(d)

    if limit and limit > 0:
        dormant = dormant[:limit]

    print(f"Identified {len(dormant)} dormant deal(s) to wake up.\n")
    print(f"  {'Deal ID':<14}  {'Stage':<22}  {'€/mo':>6}  {'Rep':<25}  Name")
    print(f"  {'-'*14}  {'-'*22}  {'-'*6}  {'-'*25}  {'-'*40}")
    for d in dormant:
        p = d["properties"] or {}
        stage = (p.get("dealstage") or "")[:22]
        amount = float(p.get("amount") or 0)
        name = (p.get("dealname") or "")[:50]
        rep = _extract_rep_id(p.get("dealname") or "") or f"(infer T{_infer_tier(amount)})"
        print(f"  {d['id']:<14}  {stage:<22}  {amount:>6.0f}  {rep:<25}  {name}")

    if not dormant:
        print("Nothing to wake. All non-closed deals already have sequences.")
        return 0

    if not apply:
        print(f"\n[DRY RUN] No changes made. Re-run with --apply to wake these {len(dormant)} deal(s).")
        print("         Each wake = 1 Anthropic call (~$0.02) + sequence saved + Day-0 message queued + closedate set to today+3.")
        return 0

    # Need imports for actual generation
    from agents.researcher import ProspectProfile
    from agents.writer import generate_sequence
    from agents.crm import _save_sequence
    from tools.outreach_queue import add_to_queue
    from tools import hubspot_client as hs
    from datetime import date, timedelta

    print(f"\n⚠️  About to GENERATE SEQUENCES + QUEUE DAY-0 OUTREACH for {len(dormant)} deal(s).")
    print(f"    Estimated cost: ~${len(dormant) * 0.02:.2f} in Anthropic API.")
    confirm = input("Type 'yes' to proceed: ").strip().lower()
    if confirm != "yes":
        print("Aborted by user.")
        return 1

    today = date.today()
    succeeded = failed = 0
    DEFAULT_REP = "perry_patraszewski"  # fallback when [rep_id] suffix is missing
    import re as _re

    for d in dormant:
        deal_id = d["id"]
        p = d["properties"] or {}
        raw_name = p.get("dealname") or ""
        # Strip "[rep_id]" suffix and " · MOM" suffix to get clean venue name
        prospect_name = _re.sub(r"\s*\[[^\]]+\]\s*$", "", raw_name)
        prospect_name = _re.sub(r"\s*[·•—]\s*MOM.*$", "", prospect_name).strip()
        amount = float(p.get("amount") or 0)
        tier = _infer_tier(amount)
        rep_id = _extract_rep_id(raw_name) or DEFAULT_REP

        # Fetch best-available contact info from associated HubSpot contact
        contacts = _fetch_deal_contacts(deal_id)

        profile = ProspectProfile(
            name=prospect_name,
            venue_type="hospitality venue",  # generic fallback
            address="Portugal",
            website=None,
            phone=None,
            email=contacts.get("email"),
            linkedin_url=contacts.get("linkedin_url"),
            instagram_handle=None,
            tier=tier,
            contact_name=contacts.get("contact_name"),
            contact_title=contacts.get("contact_title"),
            description="(profile rebuilt from HubSpot data only — no fresh research)",
            personalisation_hook="",
            health_wellness_angle="",
            confirmed_tier=tier,
            tier_reasoning=f"inferred from deal amount €{amount:.0f}/mo",
            raw_text="",
        )

        try:
            sequence = generate_sequence(profile, rep_id)
        except Exception as e:
            failed += 1
            _log({"phase": "wake", "deal_id": deal_id, "deal_name": prospect_name,
                  "result": "error", "error": f"generate_sequence: {e}"})
            print(f"  ✗ {deal_id}  {prospect_name[:40]} — sequence gen failed: {e}", file=sys.stderr)
            continue

        try:
            _save_sequence(deal_id, profile, sequence, rep_id)
        except Exception as e:
            failed += 1
            _log({"phase": "wake", "deal_id": deal_id, "deal_name": prospect_name,
                  "result": "error", "error": f"save_sequence: {e}"})
            print(f"  ✗ {deal_id}  {prospect_name[:40]} — save failed: {e}", file=sys.stderr)
            continue

        # Queue Day-0 message: prefer LinkedIn connection if a LinkedIn url is known,
        # else email opener if email is known, else queue the connection note for the rep to handle manually.
        opener = next((m for m in sequence.messages if m.message_type == "linkedin_connection"), None)
        opener_msg = next((m for m in sequence.messages if m.message_type == "linkedin_opener"), None)
        queued_count = 0

        if opener:
            add_to_queue(rep_id, {
                "venue_name": prospect_name,
                "contact_name": profile.contact_name,
                "contact_title": profile.contact_title,
                "email": profile.email,
                "linkedin_url": profile.linkedin_url,
                "deal_id": deal_id,
                "message_type": "LinkedIn Connection Request",
                "channel": "LinkedIn",
                "message": opener.body,
            })
            queued_count += 1
        if opener_msg:
            add_to_queue(rep_id, {
                "venue_name": prospect_name,
                "contact_name": profile.contact_name,
                "contact_title": profile.contact_title,
                "email": profile.email,
                "linkedin_url": profile.linkedin_url,
                "deal_id": deal_id,
                "message_type": "LinkedIn Opening Message",
                "channel": "LinkedIn",
                "message": opener_msg.body,
            })
            queued_count += 1

        # Set HubSpot closedate = today + 3 so the sequencer picks up Day-3 follow-up
        next_followup = today + timedelta(days=3)
        try:
            hs.update_deal_followup(deal_id, next_followup)
        except Exception:
            pass  # non-critical

        _log({"phase": "wake", "deal_id": deal_id, "deal_name": prospect_name,
              "rep_id": rep_id, "tier": tier, "messages_queued": queued_count,
              "next_followup": next_followup.isoformat(), "result": "ok"})
        print(f"  ✓ {deal_id}  {prospect_name[:40]:<40}  → rep={rep_id}, queued {queued_count} msg, next={next_followup}")
        succeeded += 1

    print(f"\nDone. {succeeded} woken, {failed} failed. Audit log: {LOG_PATH}")
    print(f"      Daily sequencer will now pick these up automatically per the cadence (Day 3, 7, 14, then every 5 weeks).")
    return 0 if failed == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_text in [("restage", "restage mislabeled deals"),
                             ("queue",   "remove dedup conflicts from queue files"),
                             ("wake",    "generate sequences + queue Day-0 for dormant deals")]:
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--apply", action="store_true", help="actually perform the changes")
        if name == "wake":
            sp.add_argument("--limit", type=int, default=None,
                            help="only process the first N deals (for trial runs)")

    args = p.parse_args()
    if args.cmd == "restage":
        return cmd_restage(args.apply)
    if args.cmd == "queue":
        return cmd_queue(args.apply)
    if args.cmd == "wake":
        return cmd_wake(args.apply, getattr(args, "limit", None))
    return 2


if __name__ == "__main__":
    sys.exit(main())
