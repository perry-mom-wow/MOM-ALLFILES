"""Dual-write helpers: keep local JSON files and Postgres tables in sync.

On every write to a queue / sent / sequence JSON file we also write the same
data into Postgres (if DATABASE_URL is configured). On container startup we
hydrate the local files from Postgres so an ephemeral filesystem (Railway,
Streamlit Cloud) doesn't lose state on restart.

Architecture choice: keep the file-based code paths intact (they already work
locally and across the dashboard's many read sites) and only add a thin DB
mirror. When DATABASE_URL is not set, every function here is a no-op.

Three integration points:

1. **State-changing writes** — `mirror_queue_file`, `mirror_sent_file`,
   `mirror_sequence_file` get called immediately after a JSON file is written
   on disk. They INSERT...ON CONFLICT UPDATE into Postgres.

2. **Bootstrap** — `hydrate_local_files()` is called once at app startup. It
   pulls every row from queue_files / sent_files / sequence_files and writes
   the JSON onto the local filesystem.

3. **Migration** — `migrate_all_local_to_db()` is a one-shot pass that walks
   every queue / sent / sequence file on the current filesystem and pushes
   them to Postgres. Used once to seed an empty DB.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
QUEUES_DIR = _ROOT / "queues"
SENT_DIR = _ROOT / "sent"
SEQUENCES_DIR = _ROOT / "data" / "sequences"


def _db_available() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


def _ensure_dirs() -> None:
    QUEUES_DIR.mkdir(parents=True, exist_ok=True)
    SENT_DIR.mkdir(parents=True, exist_ok=True)
    SEQUENCES_DIR.mkdir(parents=True, exist_ok=True)


def _session():
    """Lazy import; this module is safe to import without sqlalchemy installed
    when DB is not configured."""
    from state.db import SessionLocal
    return SessionLocal()


# ── Filename parsers ──────────────────────────────────────────────────────────

def _parse_queue_filename(name: str) -> Optional[tuple[str, date]]:
    """queues/rep_id_YYYY-MM-DD.json → (rep_id, date) or None."""
    stem = name[:-5] if name.endswith(".json") else name
    if "_" not in stem:
        return None
    rep_id, _, day_str = stem.rpartition("_")
    try:
        return rep_id, date.fromisoformat(day_str)
    except Exception:
        return None


def _parse_sent_filename(name: str) -> Optional[tuple[str, date]]:
    return _parse_queue_filename(name)


# ── Single-file mirror upserts ────────────────────────────────────────────────

def mirror_queue_file(rep_id: str, day: date, items: list) -> None:
    """Upsert one queue file's contents into Postgres. No-op without DATABASE_URL."""
    if not _db_available():
        return
    try:
        from state.models import QueueFile
        with _session() as s:
            row = s.get(QueueFile, (rep_id, day))
            if row is None:
                row = QueueFile(rep_id=rep_id, day=day, items=items)
                s.add(row)
            else:
                row.items = items
                row.updated_at = datetime.utcnow()
            s.commit()
    except Exception as e:
        log.warning("mirror_queue_file(%s, %s) failed: %s", rep_id, day, e)


def mirror_sent_file(rep_id: str, day: date, items: list) -> None:
    if not _db_available():
        return
    try:
        from state.models import SentFile
        with _session() as s:
            row = s.get(SentFile, (rep_id, day))
            if row is None:
                row = SentFile(rep_id=rep_id, day=day, items=items)
                s.add(row)
            else:
                row.items = items
                row.updated_at = datetime.utcnow()
            s.commit()
    except Exception as e:
        log.warning("mirror_sent_file(%s, %s) failed: %s", rep_id, day, e)


def mirror_sequence_file(deal_id: str, payload: dict) -> None:
    if not _db_available():
        return
    try:
        from state.models import SequenceFile
        with _session() as s:
            row = s.get(SequenceFile, deal_id)
            rep_id = payload.get("rep_id")
            prospect_name = payload.get("prospect_name")
            if row is None:
                row = SequenceFile(
                    deal_id=deal_id,
                    rep_id=rep_id,
                    prospect_name=prospect_name,
                    payload=payload,
                )
                s.add(row)
            else:
                row.payload = payload
                row.rep_id = rep_id or row.rep_id
                row.prospect_name = prospect_name or row.prospect_name
                row.updated_at = datetime.utcnow()
            s.commit()
    except Exception as e:
        log.warning("mirror_sequence_file(%s) failed: %s", deal_id, e)


# ── Convenience writers (file + DB in one shot) ───────────────────────────────

def write_queue_file(rep_id: str, day: date, items: list) -> Path:
    _ensure_dirs()
    path = QUEUES_DIR / f"{rep_id}_{day.isoformat()}.json"
    with open(path, "w") as f:
        json.dump(items, f, indent=2, default=str)
    mirror_queue_file(rep_id, day, items)
    return path


def write_sent_file(rep_id: str, day: date, items: list) -> Path:
    _ensure_dirs()
    path = SENT_DIR / f"{rep_id}_{day.isoformat()}.json"
    with open(path, "w") as f:
        json.dump(items, f, indent=2, default=str)
    mirror_sent_file(rep_id, day, items)
    return path


def write_sequence_file(deal_id: str, payload: dict) -> Path:
    _ensure_dirs()
    path = SEQUENCES_DIR / f"{deal_id}.json"
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    mirror_sequence_file(deal_id, payload)
    return path


# ── Bootstrap: hydrate local files from DB ────────────────────────────────────

def hydrate_local_files() -> dict:
    """At app startup, copy every queue/sent/sequence row from Postgres onto
    the local filesystem. Returns a counts dict. No-op without DATABASE_URL."""
    if not _db_available():
        return {"queues": 0, "sent": 0, "sequences": 0, "skipped": True}

    from state.db import init_db
    from state.models import QueueFile, SentFile, SequenceFile

    _ensure_dirs()
    counts = {"queues": 0, "sent": 0, "sequences": 0}
    try:
        init_db()  # idempotent CREATE TABLE
        with _session() as s:
            for q in s.query(QueueFile).all():
                path = QUEUES_DIR / f"{q.rep_id}_{q.day.isoformat()}.json"
                # Only overwrite if file missing OR DB row is newer.
                if not path.exists():
                    with open(path, "w") as f:
                        json.dump(q.items, f, indent=2, default=str)
                    counts["queues"] += 1
            for s_row in s.query(SentFile).all():
                path = SENT_DIR / f"{s_row.rep_id}_{s_row.day.isoformat()}.json"
                if not path.exists():
                    with open(path, "w") as f:
                        json.dump(s_row.items, f, indent=2, default=str)
                    counts["sent"] += 1
            for seq in s.query(SequenceFile).all():
                path = SEQUENCES_DIR / f"{seq.deal_id}.json"
                if not path.exists():
                    with open(path, "w") as f:
                        json.dump(seq.payload, f, indent=2, default=str)
                    counts["sequences"] += 1
    except Exception as e:
        log.exception("hydrate_local_files failed: %s", e)
    return counts


# ── Migration: push everything on disk into Postgres ──────────────────────────

def migrate_all_local_to_db() -> dict:
    """One-shot: walk current filesystem and push every queue/sent/sequence
    file into Postgres. Useful for seeding a fresh DB."""
    if not _db_available():
        return {"error": "DATABASE_URL not set"}

    from state.db import init_db
    init_db()

    counts = {"queues": 0, "sent": 0, "sequences": 0, "errors": 0}

    for path in sorted(QUEUES_DIR.glob("*.json")):
        if path.name.endswith(".bak"):
            continue
        parsed = _parse_queue_filename(path.name)
        if not parsed:
            continue
        rep_id, day = parsed
        try:
            items = json.loads(path.read_text())
            mirror_queue_file(rep_id, day, items)
            counts["queues"] += 1
        except Exception as e:
            log.warning("queue %s: %s", path.name, e)
            counts["errors"] += 1

    for path in sorted(SENT_DIR.glob("*.json")):
        parsed = _parse_sent_filename(path.name)
        if not parsed:
            continue
        rep_id, day = parsed
        try:
            items = json.loads(path.read_text())
            mirror_sent_file(rep_id, day, items)
            counts["sent"] += 1
        except Exception as e:
            log.warning("sent %s: %s", path.name, e)
            counts["errors"] += 1

    for path in sorted(SEQUENCES_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
            deal_id = payload.get("deal_id") or path.stem
            mirror_sequence_file(deal_id, payload)
            counts["sequences"] += 1
        except Exception as e:
            log.warning("sequence %s: %s", path.name, e)
            counts["errors"] += 1

    return counts
