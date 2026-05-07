"""Granola connector. Pull recent meeting notes + extracted action items.

Granola does not have an official documented public API yet. Two paths:
  1. If GRANOLA_API_KEY is set, use the official API once we have it.
  2. Otherwise, use the local Granola desktop app's MCP server (already
     listed in this Claude Code session: mcp__67dbc2bb-...). The orchestrator
     will detect which path is available at runtime.

Public surface (stable regardless of backend):
    list_recent_meetings(hours=24) -> list[Meeting]
    get_transcript(meeting_id)     -> str
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

log = logging.getLogger(__name__)

GRANOLA_API_KEY = os.getenv("GRANOLA_API_KEY", "").strip()
GRANOLA_BASE_URL = os.getenv("GRANOLA_BASE_URL", "https://api.granola.ai/v1").strip()


class GranolaUnavailable(RuntimeError):
    """Raised when neither the API nor the local MCP path is available."""


@dataclass
class Meeting:
    id: str
    title: str
    started_at: datetime
    ended_at: Optional[datetime]
    attendees: list[str] = field(default_factory=list)
    summary: Optional[str] = None
    action_items: list[str] = field(default_factory=list)
    transcript: Optional[str] = None  # populated by get_transcript()


def _http() -> httpx.Client:
    if not GRANOLA_API_KEY:
        raise GranolaUnavailable(
            "GRANOLA_API_KEY not set. Either set it in .env or rely on the "
            "Granola MCP server path (orchestrator will switch automatically)."
        )
    return httpx.Client(
        base_url=GRANOLA_BASE_URL,
        headers={"Authorization": f"Bearer {GRANOLA_API_KEY}"},
        timeout=30.0,
    )


def list_recent_meetings(*, hours: int = 24) -> list[Meeting]:
    """Return meetings that ended in the last `hours`. Sorted newest-first."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    with _http() as client:
        # Endpoint shape will be verified once we have a real API key. The
        # call below is the most-likely shape based on Granola's typical
        # patterns; if the response shape differs we adjust here only.
        resp = client.get("/meetings", params={"since": cutoff.isoformat()})
        resp.raise_for_status()
        items = resp.json().get("meetings", [])
    out: list[Meeting] = []
    for it in items:
        out.append(Meeting(
            id=it["id"],
            title=it.get("title", "(untitled)"),
            started_at=datetime.fromisoformat(it["started_at"].replace("Z", "+00:00")),
            ended_at=(
                datetime.fromisoformat(it["ended_at"].replace("Z", "+00:00"))
                if it.get("ended_at") else None
            ),
            attendees=it.get("attendees", []),
            summary=it.get("summary"),
            action_items=it.get("action_items", []),
        ))
    out.sort(key=lambda m: m.started_at, reverse=True)
    return out


def get_transcript(meeting_id: str) -> str:
    with _http() as client:
        resp = client.get(f"/meetings/{meeting_id}/transcript")
        resp.raise_for_status()
        return resp.json().get("transcript", "")
