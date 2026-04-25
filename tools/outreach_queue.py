"""Manage the daily outreach queue — messages ready for reps to send."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

QUEUE_DIR = Path(__file__).parent.parent / "queues"
QUEUE_DIR.mkdir(exist_ok=True)


def _queue_path(rep_id: str, day: date) -> Path:
    return QUEUE_DIR / f"{rep_id}_{day.isoformat()}.json"


def add_to_queue(rep_id: str, item: dict, day: date | None = None) -> None:
    """Add a message item to a rep's daily queue."""
    day = day or date.today()
    path = _queue_path(rep_id, day)
    items = load_queue(rep_id, day)
    items.append(item)
    with open(path, "w") as f:
        json.dump(items, f, indent=2, default=str)


def load_queue(rep_id: str, day: date | None = None) -> list[dict]:
    day = day or date.today()
    path = _queue_path(rep_id, day)
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def clear_queue(rep_id: str, day: date | None = None) -> None:
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
