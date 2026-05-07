"""HubSpot connector — Phase 1 read-only wrapper for drift detection.

Wraps the existing tools/hubspot_client.py so the EA can read companies,
contacts, and deals without ever writing back. Per spec §10, Phase 1 surfaces
discrepancies (e.g., HubSpot says "stage: appointment scheduled" but the
inbox shows the deal was won 3 weeks ago) but never updates HubSpot.

Phase 2 will add write operations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from tools import hubspot_client as _hs


@dataclass
class HSContact:
    id: str
    email: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    company_id: Optional[str]
    raw: dict = field(default_factory=dict)


@dataclass
class HSDeal:
    id: str
    name: str
    stage: Optional[str]
    amount: Optional[float]
    close_date: Optional[str]
    owner_id: Optional[str]
    raw: dict = field(default_factory=dict)


@dataclass
class HSCompany:
    id: str
    name: Optional[str]
    domain: Optional[str]
    raw: dict = field(default_factory=dict)


def list_deals(*, include_stages: Optional[list[str]] = None) -> list[HSDeal]:
    raw_deals = _hs.get_all_deals()
    out: list[HSDeal] = []
    for d in raw_deals:
        props = d.get("properties", {})
        stage = (props.get("dealstage") or "").lower()
        if include_stages and stage not in [s.lower() for s in include_stages]:
            continue
        amount_raw = props.get("amount")
        try:
            amount = float(amount_raw) if amount_raw else None
        except (TypeError, ValueError):
            amount = None
        out.append(HSDeal(
            id=d.get("id") or d.get("hs_object_id") or "",
            name=props.get("dealname") or "",
            stage=stage,
            amount=amount,
            close_date=props.get("closedate"),
            owner_id=props.get("hubspot_owner_id"),
            raw=d,
        ))
    return out


def find_deal_by_email(email: str) -> Optional[HSDeal]:
    """Find a deal whose primary contact has this email."""
    if not email:
        return None
    deals = list_deals()
    for d in deals:
        if email.lower() in (d.raw.get("properties", {}).get("email") or "").lower():
            return d
    return None


def list_open_deals_with_no_recent_activity(*, days_stale: int = 14) -> list[HSDeal]:
    """Surface deals stuck for >N days. Used for stale-thread detection in the brief."""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_stale)
    out: list[HSDeal] = []
    for d in list_deals():
        last_modified = d.raw.get("properties", {}).get("hs_lastmodifieddate")
        if not last_modified:
            continue
        try:
            ts = datetime.fromisoformat(last_modified.replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < cutoff and d.stage not in ("won", "lost", "closed"):
            out.append(d)
    return out
