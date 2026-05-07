"""Conversation-state tracker for active deals.

Replaces the binary "🛑 They Replied → stop forever" model with a ping-pong
ownership tracker. Every deal in conversation tracks who owes whom and when
the next nudge should fire.

State (lives in data/sequences/<deal_id>.json under the `conversation` key):

    {
      "last_outbound_at": "2026-05-07T09:30:00Z",
      "last_inbound_at":  "2026-05-06T14:00:00Z",
      "nudge_count": 0,
      "next_nudge_after": "2026-05-10T09:30:00Z",
      "paused_until": null,
      "terminal_state": null,            # "won" | "lost" | None
      "started_tracking_at": "2026-05-06T14:00:00Z"
    }

Cadence (Perry's spec, 2026-05-07):
    Active phase: 3, 7, 14, 21, 28 days after we last sent.
    Long-tail:    every 5 weeks (35 days) thereafter.
    Hard cap:     365 days from started_tracking_at, then propose marking lost.

Ownership:
    last_inbound_at  > last_outbound_at  →  WE owe them (urgent — top of queue)
    last_outbound_at > last_inbound_at   →  THEY owe us (nudge after N days)
    paused_until > now                   →  silent until that date
    terminal_state set                   →  silent forever
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
SEQUENCE_DIR = _ROOT / "data" / "sequences"

# Cadence: nth-nudge offset from `last_outbound_at`. Index = nudge_count BEFORE
# the upcoming nudge fires.
_ACTIVE_DAYS: tuple[int, ...] = (3, 7, 14, 21, 28)
_LONG_TAIL_STEP_DAYS: int = 35
_HARD_CAP_DAYS: int = 365


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def next_nudge_offset(nudge_count: int) -> Optional[timedelta]:
    """Days after `last_outbound_at` when the next nudge should surface.

    Returns None when the cadence has run past the 12-month cap — caller
    should propose marking the deal lost rather than nudge again.
    """
    if nudge_count < len(_ACTIVE_DAYS):
        return timedelta(days=_ACTIVE_DAYS[nudge_count])
    long_tail_idx = nudge_count - len(_ACTIVE_DAYS) + 1
    days = _ACTIVE_DAYS[-1] + _LONG_TAIL_STEP_DAYS * long_tail_idx
    if days > _HARD_CAP_DAYS:
        return None
    return timedelta(days=days)


@dataclass
class ConversationState:
    deal_id: str
    last_outbound_at: Optional[datetime]
    last_inbound_at: Optional[datetime]
    nudge_count: int
    next_nudge_after: Optional[datetime]
    paused_until: Optional[datetime]
    terminal_state: Optional[str]      # "won" | "lost" | None
    started_tracking_at: Optional[datetime]

    def is_terminal(self) -> bool:
        return bool(self.terminal_state)

    def is_paused(self, *, at: Optional[datetime] = None) -> bool:
        return bool(self.paused_until and self.paused_until > (at or _now()))

    def we_owe_them(self) -> bool:
        """True iff their last message is more recent than ours (or only they
        have messaged at all)."""
        if not self.last_inbound_at:
            return False
        if not self.last_outbound_at:
            return True
        return self.last_inbound_at > self.last_outbound_at

    def days_since_last_outbound(self) -> Optional[int]:
        if not self.last_outbound_at:
            return None
        return (_now() - self.last_outbound_at).days

    def is_due_for_nudge(self, *, at: Optional[datetime] = None) -> bool:
        at = at or _now()
        if self.is_terminal() or self.is_paused(at=at):
            return False
        if self.we_owe_them():
            return True   # owed-reply state always surfaces
        if not self.next_nudge_after or not self.last_outbound_at:
            return False
        return at >= self.next_nudge_after

    def is_past_cap(self) -> bool:
        if not self.started_tracking_at:
            return False
        return (_now() - self.started_tracking_at).days > _HARD_CAP_DAYS


# ── Disk I/O ───────────────────────────────────────────────────────────────────

def _seq_path(deal_id: str) -> Path:
    return SEQUENCE_DIR / f"{deal_id}.json"


def _read(deal_id: str) -> dict:
    p = _seq_path(deal_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _write(deal_id: str, payload: dict) -> None:
    SEQUENCE_DIR.mkdir(parents=True, exist_ok=True)
    p = _seq_path(deal_id)
    with open(p, "w") as f:
        json.dump(payload, f, indent=2, default=str)


def _conv_block(seq: dict) -> dict:
    block = seq.get("conversation") or {}
    if not isinstance(block, dict):
        return {}
    return block


def get_state(deal_id: str) -> Optional[ConversationState]:
    """Return state for a deal, or None if no tracking info on file."""
    seq = _read(deal_id)
    block = _conv_block(seq)
    if not block:
        return None
    return ConversationState(
        deal_id=deal_id,
        last_outbound_at=_parse_iso(block.get("last_outbound_at")),
        last_inbound_at=_parse_iso(block.get("last_inbound_at")),
        nudge_count=int(block.get("nudge_count") or 0),
        next_nudge_after=_parse_iso(block.get("next_nudge_after")),
        paused_until=_parse_iso(block.get("paused_until")),
        terminal_state=block.get("terminal_state"),
        started_tracking_at=_parse_iso(block.get("started_tracking_at")),
    )


def _save_state(deal_id: str, state: dict) -> None:
    seq = _read(deal_id)
    seq["conversation"] = state
    _write(deal_id, seq)


def _ensure_started(state: dict) -> dict:
    state.setdefault("started_tracking_at", _now().isoformat())
    return state


# ── State transitions ─────────────────────────────────────────────────────────

def mark_outbound(deal_id: str, *, sent_at: Optional[datetime] = None) -> ConversationState:
    """Record that WE just sent a message. Schedules the next nudge.

    Increments `nudge_count` if we already had outbound queued (so back-to-back
    sends don't collapse the cadence). Resets to 0 if THEY had replied since
    our last outbound — fresh ping-pong leg.
    """
    sent_at = sent_at or _now()
    seq = _read(deal_id)
    block = _conv_block(seq)
    last_outbound = _parse_iso(block.get("last_outbound_at"))
    last_inbound = _parse_iso(block.get("last_inbound_at"))

    # If they replied after our previous send, this is the start of a fresh
    # leg — reset the nudge counter.
    if last_inbound and (not last_outbound or last_inbound > last_outbound):
        nudge_count = 0
    else:
        nudge_count = int(block.get("nudge_count") or 0)

    offset = next_nudge_offset(nudge_count)
    next_after = (sent_at + offset).isoformat() if offset else None

    new_block = _ensure_started({
        **block,
        "last_outbound_at": sent_at.isoformat(),
        "nudge_count": nudge_count + 1,
        "next_nudge_after": next_after,
        "paused_until": None,           # any pause cleared by manual outbound
        "terminal_state": None,         # never override terminal here
    })
    if block.get("terminal_state"):
        # If a terminal state was set, leave it alone — caller must explicitly
        # un-terminate via reopen() before sending again.
        new_block["terminal_state"] = block["terminal_state"]

    _save_state(deal_id, new_block)
    return get_state(deal_id)  # type: ignore[return-value]


def mark_inbound(deal_id: str, *, received_at: Optional[datetime] = None) -> ConversationState:
    """Record that THEY just replied. Flips ownership to us. Clears nudge schedule."""
    received_at = received_at or _now()
    seq = _read(deal_id)
    block = _conv_block(seq)
    new_block = _ensure_started({
        **block,
        "last_inbound_at": received_at.isoformat(),
        "next_nudge_after": None,
        "nudge_count": 0,
        "terminal_state": None,
    })
    _save_state(deal_id, new_block)
    return get_state(deal_id)  # type: ignore[return-value]


def mark_terminal(deal_id: str, terminal: str) -> ConversationState:
    """Close the deal. terminal: 'won' | 'lost'."""
    if terminal not in ("won", "lost"):
        raise ValueError(f"Invalid terminal state: {terminal!r}")
    seq = _read(deal_id)
    block = _conv_block(seq)
    new_block = _ensure_started({
        **block,
        "terminal_state": terminal,
        "next_nudge_after": None,
    })
    _save_state(deal_id, new_block)
    return get_state(deal_id)  # type: ignore[return-value]


def pause_until(deal_id: str, until: date) -> ConversationState:
    until_dt = datetime(until.year, until.month, until.day, 9, 0, tzinfo=timezone.utc)
    seq = _read(deal_id)
    block = _conv_block(seq)
    new_block = _ensure_started({
        **block,
        "paused_until": until_dt.isoformat(),
        "next_nudge_after": None,
    })
    _save_state(deal_id, new_block)
    return get_state(deal_id)  # type: ignore[return-value]


def reopen(deal_id: str) -> ConversationState:
    """Clear terminal state and pause; resume tracking from now as a fresh leg."""
    seq = _read(deal_id)
    block = _conv_block(seq)
    new_block = _ensure_started({
        **block,
        "terminal_state": None,
        "paused_until": None,
        "nudge_count": 0,
        "next_nudge_after": None,
    })
    _save_state(deal_id, new_block)
    return get_state(deal_id)  # type: ignore[return-value]


# ── Discovery ─────────────────────────────────────────────────────────────────

def iter_states() -> Iterator[ConversationState]:
    """Walk every sequence file with conversation tracking on it."""
    if not SEQUENCE_DIR.exists():
        return
    for p in SEQUENCE_DIR.glob("*.json"):
        deal_id = p.stem
        s = get_state(deal_id)
        if s is not None:
            yield s


def due_nudges(*, rep_id: Optional[str] = None, at: Optional[datetime] = None) -> list[dict]:
    """Return the deals that need attention right now.

    Each item: {deal_id, prospect_name, contact_name, contact_email,
    linkedin_url, kind, days_overdue, nudge_count, past_cap, owner_rep}
      kind = 'we_owe_them' | 'nudge_them' | 'past_cap'
    """
    at = at or _now()
    out: list[dict] = []
    for state in iter_states():
        if state.is_terminal() or state.is_paused(at=at):
            continue
        seq = _read(state.deal_id)
        if rep_id and seq.get("rep_id") not in (None, rep_id):
            continue
        if not state.is_due_for_nudge(at=at):
            continue

        if state.we_owe_them():
            kind = "we_owe_them"
            days_overdue = (at - state.last_inbound_at).days if state.last_inbound_at else 0
        elif state.is_past_cap():
            kind = "past_cap"
            days_overdue = state.days_since_last_outbound() or 0
        else:
            kind = "nudge_them"
            days_overdue = (
                (at - state.next_nudge_after).days
                if state.next_nudge_after else 0
            )

        out.append({
            "deal_id": state.deal_id,
            "prospect_name": seq.get("prospect_name") or "Unknown",
            "contact_name": seq.get("contact_name"),
            "contact_email": seq.get("contact_email"),
            "linkedin_url": seq.get("linkedin_url"),
            "phone": seq.get("phone"),
            "instagram_handle": seq.get("instagram_handle"),
            "rep_id": seq.get("rep_id"),
            "kind": kind,
            "days_overdue": max(0, days_overdue),
            "nudge_count": state.nudge_count,
            "past_cap": state.is_past_cap(),
            "last_outbound_at": state.last_outbound_at.isoformat() if state.last_outbound_at else None,
            "last_inbound_at": state.last_inbound_at.isoformat() if state.last_inbound_at else None,
            "intent": seq.get("intent"),
        })

    # Order: we-owe-them first (most urgent), then by days_overdue desc.
    kind_order = {"we_owe_them": 0, "past_cap": 1, "nudge_them": 2}
    out.sort(key=lambda d: (kind_order.get(d["kind"], 9), -d["days_overdue"]))
    return out
