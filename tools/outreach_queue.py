"""Manage the daily outreach queue — messages ready for reps to send."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

QUEUE_DIR = Path(__file__).parent.parent / "queues"
QUEUE_DIR.mkdir(exist_ok=True)
SENT_DIR = Path(__file__).parent.parent / "sent"
SENT_DIR.mkdir(exist_ok=True)


def _queue_path(rep_id: str, day: date) -> Path:
    return QUEUE_DIR / f"{rep_id}_{day.isoformat()}.json"


def _sent_path(rep_id: str, day: date) -> Path:
    return SENT_DIR / f"{rep_id}_{day.isoformat()}.json"


def add_to_queue(rep_id: str, item: dict, day: Optional[date] = None) -> None:
    """Add a message item to a rep's daily queue."""
    day = day or date.today()
    path = _queue_path(rep_id, day)
    items = load_queue(rep_id, day)
    items.append(item)
    with open(path, "w") as f:
        json.dump(items, f, indent=2, default=str)


def load_queue(rep_id: str, day: Optional[date] = None) -> list[dict]:
    day = day or date.today()
    path = _queue_path(rep_id, day)
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def remove_from_queue(rep_id: str, index: int, day: Optional[date] = None) -> None:
    """Remove a single item by index from the queue (e.g. after it's been sent)."""
    day = day or date.today()
    path = _queue_path(rep_id, day)
    items = load_queue(rep_id, day)
    if 0 <= index < len(items):
        items.pop(index)
    if items:
        with open(path, "w") as f:
            json.dump(items, f, indent=2, default=str)
    elif path.exists():
        path.unlink()


def load_pending(rep_id: str, days_back: int = 14) -> list[dict]:
    """Return all unsent items for a rep across the last N days (today + older).

    Each item is annotated with a `_source_date` (ISO string) so callers know
    which queue file it came from for marking-as-sent.
    """
    today = date.today()
    items: list[dict] = []
    for offset in range(days_back + 1):
        day = today - timedelta(days=offset)
        for raw in load_queue(rep_id, day):
            item = dict(raw)
            item["_source_date"] = day.isoformat()
            items.append(item)
    # Today first, then older days oldest-first within
    items.sort(key=lambda i: (i["_source_date"] != today.isoformat(), i["_source_date"]))
    return items


def log_sent(rep_id: str, item: dict, day: Optional[date] = None) -> None:
    """Append a sent item to today's `sent/<rep>_<date>.json` log."""
    day = day or date.today()
    path = _sent_path(rep_id, day)
    log: list[dict] = []
    if path.exists():
        try:
            log = json.loads(path.read_text())
        except Exception:
            log = []
    log.append({k: v for k, v in item.items() if k != "_source_date"})
    with open(path, "w") as f:
        json.dump(log, f, indent=2, default=str)


def load_sent(rep_id: str, day: Optional[date] = None) -> list[dict]:
    day = day or date.today()
    path = _sent_path(rep_id, day)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def remove_pending_item(rep_id: str, item: dict) -> None:
    """Remove an item from its source-date queue, identified by deal_id + message_type."""
    src = item.get("_source_date")
    if not src:
        return
    src_date = date.fromisoformat(src)
    items = load_queue(rep_id, src_date)
    deal_id = item.get("deal_id")
    msg_type = item.get("message_type")
    new_items = [
        it for it in items
        if not (it.get("deal_id") == deal_id and it.get("message_type") == msg_type)
    ]
    path = _queue_path(rep_id, src_date)
    if new_items:
        with open(path, "w") as f:
            json.dump(new_items, f, indent=2, default=str)
    elif path.exists():
        path.unlink()


def clear_queue(rep_id: str, day: Optional[date] = None) -> None:
    day = day or date.today()
    path = _queue_path(rep_id, day)
    if path.exists():
        path.unlink()


def format_queue_for_display(items: list[dict]) -> str:
    """Return a human-readable queue summary for CLI or dashboard."""
    if not items:
        return "No messages in queue."
    lines = []
    for i, item in enumerate(items, 1):
        lines.append(
            f"[{i}] {item.get('venue_name', 'Unknown')} — {item.get('message_type', 'message')}\n"
            f"    Channel: {item.get('channel', 'LinkedIn')}\n"
            f"    --- Message ---\n"
            f"{item.get('message', '')}\n"
        )
    return "\n".join(lines)
