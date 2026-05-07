"""Google Calendar connector. Read events, write events, defend blocks.

Phase 1: read-only event listing for the daily brief's 7-day look-ahead.
Phase 2 will add writeback for hard blocks (no-meetings-before-09:30,
Friday-afternoon protect, weekend defence).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from connectors._google_auth import GoogleAuthMissing, get_credentials


@dataclass
class CalEvent:
    id: str
    calendar_id: str
    summary: str
    description: Optional[str]
    location: Optional[str]
    start: datetime
    end: datetime
    attendees: list[str]
    organizer_email: Optional[str]
    is_recurring: bool
    is_allday: bool


def _service():
    try:
        from googleapiclient.discovery import build
    except ImportError as e:
        raise GoogleAuthMissing(
            "google-api-python-client not installed. Run: pip install google-api-python-client"
        ) from e
    return build("calendar", "v3", credentials=get_credentials(), cache_discovery=False)


def _parse_dt(field: dict) -> tuple[datetime, bool]:
    """Returns (datetime, is_allday)."""
    if "dateTime" in field:
        return datetime.fromisoformat(field["dateTime"].replace("Z", "+00:00")), False
    # all-day: date only
    return datetime.fromisoformat(field["date"]).replace(tzinfo=timezone.utc), True


def list_calendars() -> list[dict]:
    svc = _service()
    return svc.calendarList().list().execute().get("items", [])


def list_events(
    *,
    calendar_id: str = "primary",
    days_ahead: int = 7,
    days_back: int = 0,
) -> list[CalEvent]:
    svc = _service()
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days_back)).isoformat()
    time_max = (now + timedelta(days=days_ahead)).isoformat()
    resp = svc.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=250,
    ).execute()
    out: list[CalEvent] = []
    for ev in resp.get("items", []):
        if ev.get("status") == "cancelled":
            continue
        start, allday = _parse_dt(ev["start"])
        end, _ = _parse_dt(ev["end"])
        out.append(CalEvent(
            id=ev["id"],
            calendar_id=calendar_id,
            summary=ev.get("summary", "(no title)"),
            description=ev.get("description"),
            location=ev.get("location"),
            start=start,
            end=end,
            attendees=[a.get("email", "") for a in ev.get("attendees", [])],
            organizer_email=(ev.get("organizer") or {}).get("email"),
            is_recurring=bool(ev.get("recurringEventId")),
            is_allday=allday,
        ))
    return out


def list_all_events(*, days_ahead: int = 7) -> list[CalEvent]:
    """Walk every calendar the user has access to (primary + Mom Founders shared, etc.)."""
    out: list[CalEvent] = []
    for cal in list_calendars():
        try:
            out.extend(list_events(calendar_id=cal["id"], days_ahead=days_ahead))
        except Exception:
            continue
    return out
