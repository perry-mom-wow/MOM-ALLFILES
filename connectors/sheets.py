"""Google Sheets connector — Tier 1 / Tier 2 contact sheet only (Phase 1).

Loads tier-context.xlsx (actually a Google Sheet) at the locked Sheet ID
1ueVrV9Nt4bQ6XyXvrvZJzgMZXB-1BzCvBXXMLZFiZU8 once per daily run, returns
typed Tier1Contact / Tier2Contact records.

The triage layer uses these to detect Tier 1 senders (never auto-draft) and
Tier 2 senders (EA drafts).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from connectors._google_auth import GoogleAuthMissing, get_credentials

log = logging.getLogger(__name__)

TIER_SHEET_ID = "1ueVrV9Nt4bQ6XyXvrvZJzgMZXB-1BzCvBXXMLZFiZU8"


@dataclass
class TierContact:
    name: str
    email: Optional[str]
    tier: int
    role: Optional[str] = None
    company: Optional[str] = None
    birthday: Optional[str] = None
    channel_preference: Optional[str] = None
    notes: Optional[str] = None
    raw: dict = field(default_factory=dict)

    def email_lower(self) -> str:
        return (self.email or "").lower()


def _service():
    try:
        from googleapiclient.discovery import build
    except ImportError as e:
        raise GoogleAuthMissing(
            "google-api-python-client not installed. Run: pip install google-api-python-client"
        ) from e
    return build("sheets", "v4", credentials=get_credentials(), cache_discovery=False)


def _read_range(sheet_name: str) -> list[list[str]]:
    svc = _service()
    resp = svc.spreadsheets().values().get(
        spreadsheetId=TIER_SHEET_ID,
        range=sheet_name,
    ).execute()
    return resp.get("values", [])


def _parse_tier_rows(rows: list[list[str]], default_tier: int) -> list[TierContact]:
    if not rows:
        return []
    headers = [h.strip().lower() for h in rows[0]]

    def get(row: list[str], key_options: tuple[str, ...]) -> Optional[str]:
        for key in key_options:
            if key in headers:
                idx = headers.index(key)
                if idx < len(row):
                    val = row[idx].strip()
                    return val or None
        return None

    out: list[TierContact] = []
    for row in rows[1:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        name = get(row, ("name", "full name")) or ""
        if not name:
            continue
        tier_raw = get(row, ("tier",))
        try:
            tier = int(tier_raw) if tier_raw else default_tier
        except ValueError:
            tier = default_tier
        out.append(TierContact(
            name=name,
            email=get(row, ("email",)),
            tier=tier,
            role=get(row, ("role", "title")),
            company=get(row, ("company", "organisation", "organization")),
            birthday=get(row, ("birthday", "dob")),
            channel_preference=get(row, ("channel", "channel preference", "preferred channel")),
            notes=get(row, ("notes", "context")),
            raw={h: row[i] if i < len(row) else "" for i, h in enumerate(headers)},
        ))
    return out


def load_tier_contacts() -> list[TierContact]:
    """Read both tabs (Tier 1, Tier 2) and merge. On error, returns []."""
    out: list[TierContact] = []
    for sheet_name, default_tier in (("Tier 1", 1), ("Tier 2", 2)):
        try:
            rows = _read_range(sheet_name)
            out.extend(_parse_tier_rows(rows, default_tier))
        except Exception as e:
            log.warning("Could not read sheet %r: %s", sheet_name, e)
    return out


def index_by_email(contacts: list[TierContact]) -> dict[str, TierContact]:
    return {c.email_lower(): c for c in contacts if c.email}
