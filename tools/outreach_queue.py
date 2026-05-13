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


CONTACT_FIELDS: tuple[str, ...] = (
    "email",
    "linkedin_url",
    "instagram_handle",
    "phone",
    "contact_name",
    "contact_title",
)


def update_contact_info(
    rep_id: str,
    deal_id: str,
    fields: dict,
    *,
    verify_linkedin: bool = True,
) -> dict:
    """Patch contact fields (email, linkedin_url, etc.) on every queued item
    for this deal across all queue files, plus the deal's sequence file.

    Save-time gate: when `linkedin_url` is in the patch and `verify_linkedin`
    is True (default), the URL is run through `verify_linkedin_url()` before
    saving. Bad URLs are dropped from the patch and reported in the result —
    the rest of the patch (email, phone, etc.) still applies.

    Returns:
        {
          "files_updated": int,
          "applied": [field, ...],
          "rejected": {field: reason, ...},  # bad fields dropped from patch
        }
    `fields` keys must be in CONTACT_FIELDS; empty/None values are ignored.
    """
    rejected: dict[str, str] = {}
    clean: dict = {k: v for k, v in fields.items() if k in CONTACT_FIELDS and v}
    if not clean or not deal_id:
        return {"files_updated": 0, "applied": [], "rejected": rejected}

    # ── Save-time gate: verify any LinkedIn URL before persisting ──────
    contact_name, venue_name = _resolve_contact_and_venue(rep_id, deal_id)
    if verify_linkedin and "linkedin_url" in clean:
        try:
            from tools.contact_finder import verify_linkedin_url
            ok, reason = verify_linkedin_url(
                clean["linkedin_url"],
                contact_name=contact_name,
                venue_name=venue_name,
            )
            if not ok:
                rejected["linkedin_url"] = reason
                clean.pop("linkedin_url")
                if not clean:
                    return {
                        "files_updated": 0,
                        "applied": [],
                        "rejected": rejected,
                    }
        except Exception as e:
            # Don't block a save on a verifier crash; flag it and keep going.
            rejected["linkedin_url_check_error"] = str(e)

    # ── Save-time gate: verify any email address before persisting ──────
    if "email" in clean:
        try:
            from tools.contact_finder import verify_email_address
            ok, severity, reason = verify_email_address(
                clean["email"],
                venue_name=venue_name,
                contact_name=contact_name,
                website=_resolve_website(deal_id),
            )
            if not ok and severity == "hard":
                rejected["email"] = reason
                clean.pop("email")
                if not clean:
                    return {
                        "files_updated": 0,
                        "applied": [],
                        "rejected": rejected,
                    }
            elif severity == "soft":
                # Save but flag in the response — UI can warn the user.
                rejected["email_soft_warning"] = reason
        except Exception as e:
            rejected["email_check_error"] = str(e)

    updated = 0
    # Walk every queue file for this rep — older queues may still hold pending
    # items for this deal (carryover) and need the same patch.
    for path in QUEUE_DIR.glob(f"{rep_id}_*.json"):
        try:
            items = json.loads(path.read_text())
        except Exception:
            continue
        changed = False
        for it in items:
            if it.get("deal_id") == deal_id:
                for k, v in clean.items():
                    if it.get(k) != v:
                        it[k] = v
                        changed = True
        if changed:
            with open(path, "w") as f:
                json.dump(items, f, indent=2, default=str)
            updated += 1

    # Mirror into the canonical sequence file so newly-generated follow-ups inherit.
    seq_path = Path(__file__).parent.parent / "data" / "sequences" / f"{deal_id}.json"
    if seq_path.exists():
        try:
            seq = json.loads(seq_path.read_text())
            seq_changed = False
            seq_field_map = {
                "email": "contact_email",
                "linkedin_url": "linkedin_url",
                "instagram_handle": "instagram_handle",
                "phone": "phone",
                "contact_name": "contact_name",
                "contact_title": "contact_title",
            }
            for k, v in clean.items():
                seq_key = seq_field_map[k]
                if seq.get(seq_key) != v:
                    seq[seq_key] = v
                    seq_changed = True
            if seq_changed:
                with open(seq_path, "w") as f:
                    json.dump(seq, f, indent=2, default=str)
                updated += 1
        except Exception:
            pass

    return {
        "files_updated": updated,
        "applied": list(clean.keys()),
        "rejected": rejected,
    }


def _resolve_website(deal_id: str) -> Optional[str]:
    """Best-effort lookup of the venue's website for the email verifier."""
    seq_path = Path(__file__).parent.parent / "data" / "sequences" / f"{deal_id}.json"
    if seq_path.exists():
        try:
            seq = json.loads(seq_path.read_text())
            return seq.get("website") or None
        except Exception:
            pass
    return None


def _resolve_contact_and_venue(rep_id: str, deal_id: str) -> tuple[Optional[str], Optional[str]]:
    """Best-effort lookup of contact_name + venue_name for a deal across the
    queue files and the sequence file. Used by the LinkedIn save-time gate."""
    contact_name: Optional[str] = None
    venue_name: Optional[str] = None
    for path in QUEUE_DIR.glob(f"{rep_id}_*.json"):
        try:
            items = json.loads(path.read_text())
        except Exception:
            continue
        for it in items:
            if it.get("deal_id") == deal_id:
                contact_name = contact_name or it.get("contact_name")
                venue_name = venue_name or it.get("venue_name")
                if contact_name and venue_name:
                    return contact_name, venue_name
    seq_path = Path(__file__).parent.parent / "data" / "sequences" / f"{deal_id}.json"
    if seq_path.exists():
        try:
            seq = json.loads(seq_path.read_text())
            contact_name = contact_name or seq.get("contact_name")
            venue_name = venue_name or seq.get("prospect_name")
        except Exception:
            pass
    return contact_name, venue_name


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
