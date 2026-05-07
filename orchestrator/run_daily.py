"""Daily run entrypoint — Phase 1 dry-run mode.

Cron schedule: 06:00 Europe/Lisbon (Modal cron wrapper TBD).

Sequence (per spec §10):
    1. Pull sources: Gmail (24h), Calendar (next 7d), Granola (24h), Brain Dump
    2. Triage Gmail threads
    3. Cross-reference (synthesizer) — to be built next milestone
    4. Draft replies for NEEDS_REPLY threads (Tier 2 + unknown senders only) — next milestone
    5. Voice-validate each draft
    6. Write drafts to Gmail Drafts folder (with `_test` label until go-live)
    7. Assemble brief, send via Resend
    8. Log to state.DailyRun
    9. Append decisions log entry to Notion Wiki

Each step is wrapped so a connector failure (missing OAuth token, API down)
degrades gracefully — the brief still sends with whatever sources succeeded,
and failures appear in the brief as a `Sources unavailable` note.

Phase 1 milestone gating:
    DRY_RUN=true  → no Gmail writes, no Notion writes, no Resend send.
                    All output to stdout + JSON snapshot under data/dry_runs/.
    DRY_RUN=false → real writes; drafts go to a `_test` Gmail label until
                    voice-validator pass rate clears 85% over 14 days.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

_ROOT = Path(__file__).parent.parent
_DRY_RUN_DIR = _ROOT / "data" / "dry_runs"
_DRY_RUN_DIR.mkdir(parents=True, exist_ok=True)


def _is_dry_run() -> bool:
    return os.getenv("DRY_RUN", "true").lower() not in ("false", "0", "no")


def _safe(label: str, fn, *args, **kwargs) -> tuple[Any, str | None]:
    """Run `fn(*args, **kwargs)`. Return (result, error_str). Never raises."""
    try:
        return fn(*args, **kwargs), None
    except Exception as e:
        log.exception("Step %s failed: %s", label, e)
        return None, f"{type(e).__name__}: {e}"


def _json_default(o: Any) -> Any:
    if is_dataclass(o):
        return asdict(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    return str(o)


def run(*, today: date | None = None) -> dict:
    today = today or date.today()
    started_at = datetime.utcnow()
    dry = _is_dry_run()

    log.info("EA daily run start | date=%s dry_run=%s", today.isoformat(), dry)

    # ── 1. Pull sources ───────────────────────────────────────────────────────
    from connectors import gmail, calendar as gcal, granola, notion, sheets

    threads, threads_err = _safe("gmail.fetch_recent_threads", gmail.fetch_recent_threads, hours=24)
    events, events_err = _safe("calendar.list_all_events", gcal.list_all_events, days_ahead=7)
    meetings, meetings_err = _safe("granola.list_recent_meetings", granola.list_recent_meetings, hours=24)
    brain_dump, brain_dump_err = _safe("notion.read_brain_dump", notion.read_brain_dump, since_hours=24)
    tier_contacts, tier_err = _safe("sheets.load_tier_contacts", sheets.load_tier_contacts)
    tier_contacts = tier_contacts or []
    tier_index = sheets.index_by_email(tier_contacts) if tier_contacts else {}

    # ── 2. Triage Gmail threads ───────────────────────────────────────────────
    from brain import triage

    triage_results: list[dict] = []
    if threads:
        for t in threads:
            latest = max(t.messages, key=lambda m: m.received_at) if t.messages else None
            if not latest:
                continue
            tier_match = tier_index.get(latest.sender_email.lower()) if tier_index else None
            ti = triage.TriageInput(
                thread_id=t.id,
                subject=t.subject,
                sender_email=latest.sender_email,
                sender_name=latest.sender_name,
                snippet=latest.snippet or latest.body_text[:500],
                is_tier1=bool(tier_match and tier_match.tier == 1),
                is_tier2=bool(tier_match and tier_match.tier == 2),
            )
            result, err = _safe(f"triage[{t.id}]", triage.classify, ti)
            if result:
                triage_results.append(result.to_dict())

    # ── 3-7. Synthesize, draft, validate, write, brief — next milestone ───────
    # (placeholder for the Week 2/3 build — gated on Perry's confirm to proceed.)

    summary = {
        "date": today.isoformat(),
        "started_at": started_at.isoformat(),
        "finished_at": datetime.utcnow().isoformat(),
        "dry_run": dry,
        "counts": {
            "threads": len(threads or []),
            "events": len(events or []),
            "meetings": len(meetings or []),
            "brain_dump_entries": len(brain_dump or []),
            "tier_contacts": len(tier_contacts),
            "triaged": len(triage_results),
        },
        "errors": {
            "gmail": threads_err,
            "calendar": events_err,
            "granola": meetings_err,
            "notion_brain_dump": brain_dump_err,
            "sheets": tier_err,
        },
        "triage_sample": triage_results[:10],
    }

    # ── 8. Log to state.DailyRun ──────────────────────────────────────────────
    if not dry:
        try:
            from state import get_session, init_db, DailyRun
            init_db()
            with get_session() as session:
                session.add(DailyRun(
                    run_date=today,
                    started_at=started_at,
                    finished_at=datetime.utcnow(),
                    threads_processed=len(threads or []),
                    drafts_created=0,  # next milestone
                    drafts_failed_voice=0,
                    commitments_extracted=0,
                    brief_sent=False,
                    errors={k: v for k, v in summary["errors"].items() if v},
                    summary=summary,
                ))
        except Exception as e:
            log.exception("Failed to log DailyRun: %s", e)

    # ── 9. Decisions log to Notion Wiki ───────────────────────────────────────
    if not dry:
        try:
            notion.append_decision_log_entry(
                title=f"Daily run {today.isoformat()}",
                body=json.dumps(summary["counts"]) + "  errors=" + json.dumps(
                    {k: v for k, v in summary["errors"].items() if v}
                ),
            )
        except Exception as e:
            log.exception("Failed to write decisions log: %s", e)

    # ── Persist dry-run snapshot for inspection ───────────────────────────────
    snapshot = _DRY_RUN_DIR / f"{today.isoformat()}.json"
    with open(snapshot, "w") as f:
        json.dump(summary, f, indent=2, default=_json_default)
    log.info("EA daily run done | snapshot=%s", snapshot)
    return summary


def _cli() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    summary = run()
    print(json.dumps(summary, indent=2, default=_json_default))


if __name__ == "__main__":
    _cli()
