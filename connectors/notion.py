"""Notion connector. Read Brain Dump, append to Wiki decisions log.

Two pages from the spec (locked IDs):
  Brain Dump: 357cbc790b02811f98c7e476ee0fa538
  Wiki:       357cbc790b02813391e9e44b89c22f95

Set NOTION_TOKEN in .env (an internal integration token from
https://www.notion.so/my-integrations). The integration must be invited to both
pages with read access (Brain Dump) and read+write (Wiki).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx

log = logging.getLogger(__name__)

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
NOTION_VERSION = "2022-06-28"
BASE_URL = "https://api.notion.com/v1"

BRAIN_DUMP_PAGE_ID = os.getenv(
    "NOTION_BRAIN_DUMP_PAGE_ID", "357cbc790b02811f98c7e476ee0fa538"
).strip()
WIKI_PAGE_ID = os.getenv(
    "NOTION_WIKI_PAGE_ID", "357cbc790b02813391e9e44b89c22f95"
).strip()


class NotionAuthMissing(RuntimeError):
    pass


@dataclass
class BrainDumpEntry:
    text: str
    created_at: datetime
    block_id: str


def _http() -> httpx.Client:
    if not NOTION_TOKEN:
        raise NotionAuthMissing(
            "NOTION_TOKEN not set. Create an integration at "
            "https://www.notion.so/my-integrations, then share the Brain Dump "
            "and Wiki pages with it."
        )
    return httpx.Client(
        base_url=BASE_URL,
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        timeout=30.0,
    )


def _block_text(block: dict) -> str:
    """Pull plain text out of any block type that has it."""
    typ = block.get("type")
    if not typ:
        return ""
    payload = block.get(typ, {})
    rich = payload.get("rich_text") or payload.get("text") or []
    return "".join(r.get("plain_text", "") for r in rich)


def _page_blocks(client: httpx.Client, page_id: str) -> list[dict]:
    """Walk all child blocks of a page, paginating."""
    out: list[dict] = []
    cursor: Optional[str] = None
    while True:
        params: dict = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = client.get(f"/blocks/{page_id}/children", params=params)
        r.raise_for_status()
        data = r.json()
        out.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return out


def read_brain_dump(*, since_hours: int = 24) -> list[BrainDumpEntry]:
    """Return all blocks created or edited in the last `since_hours`."""
    cutoff = datetime.utcnow().timestamp() - (since_hours * 3600)
    with _http() as client:
        blocks = _page_blocks(client, BRAIN_DUMP_PAGE_ID)
    out: list[BrainDumpEntry] = []
    for b in blocks:
        try:
            edited = datetime.fromisoformat(b["last_edited_time"].replace("Z", "+00:00"))
        except Exception:
            continue
        if edited.timestamp() < cutoff:
            continue
        text = _block_text(b).strip()
        if text:
            out.append(BrainDumpEntry(text=text, created_at=edited, block_id=b["id"]))
    return out


def append_decision_log_entry(
    *,
    title: str,
    body: str,
    decided_at: Optional[datetime] = None,
) -> str:
    """Append a heading + paragraph to the Wiki page. Returns the new block ID.

    Format:
        ## YYYY-MM-DD HH:MM — title
        body
    """
    decided_at = decided_at or datetime.utcnow()
    heading = f"{decided_at.strftime('%Y-%m-%d %H:%M')} — {title}"

    children = [
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": heading}}]},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": body}}]},
        },
    ]
    with _http() as client:
        r = client.patch(
            f"/blocks/{WIKI_PAGE_ID}/children",
            json={"children": children},
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        return results[0]["id"] if results else ""
