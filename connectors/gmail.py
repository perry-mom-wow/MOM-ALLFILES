"""Gmail connector. Read threads, write drafts, search, parse inbound replies.

Uses the shared Google OAuth from `connectors._google_auth`. All public methods
return plain dataclasses so callers never depend on the raw Google API surface.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

from connectors._google_auth import GoogleAuthMissing, get_credentials

log = logging.getLogger(__name__)

PERRY_EMAIL = "perry@mom-wow.com"


@dataclass
class GmailMessage:
    id: str
    thread_id: str
    sender_email: str
    sender_name: Optional[str]
    to: list[str]
    subject: str
    snippet: str
    body_text: str
    received_at: datetime
    label_ids: list[str] = field(default_factory=list)


@dataclass
class GmailThread:
    id: str
    messages: list[GmailMessage]
    last_message_at: datetime
    subject: str
    senders: list[str]


def _service():
    try:
        from googleapiclient.discovery import build
    except ImportError as e:
        raise GoogleAuthMissing(
            "google-api-python-client not installed. Run: pip install google-api-python-client"
        ) from e
    creds = get_credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _decode_part(part) -> str:
    body = part.get("body", {})
    data = body.get("data")
    if not data:
        return ""
    return base64.urlsafe_b64decode(data.encode("ascii")).decode("utf-8", errors="replace")


def _extract_text(payload) -> str:
    """Walk the MIME tree and pick the best text/plain (or text/html as fallback)."""
    if not payload:
        return ""
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        return _decode_part(payload)
    if mime.startswith("multipart/"):
        for part in payload.get("parts", []):
            text = _extract_text(part)
            if text:
                return text
    if mime == "text/html":
        # Fallback: return raw HTML; downstream snippet extraction handles it.
        return _decode_part(payload)
    return ""


def _parse_from(header: str) -> tuple[str, Optional[str]]:
    """Parse 'Name <email@domain>' → (email, name). If no angle brackets, name=None."""
    header = (header or "").strip()
    if "<" in header and ">" in header:
        name = header.split("<", 1)[0].strip().strip('"')
        email = header.split("<", 1)[1].split(">", 1)[0].strip()
        return email, name or None
    return header, None


def _msg_from_payload(msg: dict) -> GmailMessage:
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    sender_email, sender_name = _parse_from(headers.get("from", ""))
    to_field = headers.get("to", "")
    to = [t.strip() for t in to_field.split(",") if t.strip()]
    received_at = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc)
    return GmailMessage(
        id=msg["id"],
        thread_id=msg["threadId"],
        sender_email=sender_email,
        sender_name=sender_name,
        to=to,
        subject=headers.get("subject", ""),
        snippet=msg.get("snippet", "")[:500],
        body_text=_extract_text(msg.get("payload"))[:10000],
        received_at=received_at,
        label_ids=msg.get("labelIds", []),
    )


def list_threads(query: str, *, max_results: int = 50) -> list[str]:
    """Return thread IDs matching a Gmail search query (e.g., 'newer_than:1d -in:sent')."""
    svc = _service()
    resp = svc.users().threads().list(userId="me", q=query, maxResults=max_results).execute()
    return [t["id"] for t in resp.get("threads", [])]


def get_thread(thread_id: str) -> GmailThread:
    """Fetch one thread with all messages."""
    svc = _service()
    raw = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
    messages = [_msg_from_payload(m) for m in raw.get("messages", [])]
    last = max((m.received_at for m in messages), default=datetime.now(timezone.utc))
    senders = list({m.sender_email for m in messages if m.sender_email})
    subject = messages[0].subject if messages else ""
    return GmailThread(
        id=thread_id,
        messages=messages,
        last_message_at=last,
        subject=subject,
        senders=senders,
    )


def fetch_recent_threads(*, hours: int = 24, exclude_sent: bool = True) -> list[GmailThread]:
    """Pull threads with new activity in the last `hours`. Excludes sent-by-Perry threads."""
    parts = [f"newer_than:{max(1, hours // 24)}d"] if hours >= 24 else ["newer_than:1d"]
    if exclude_sent:
        parts.append("-in:sent")
    parts.append("-category:promotions")
    parts.append("-category:social")
    query = " ".join(parts)
    ids = list_threads(query, max_results=100)
    return [get_thread(tid) for tid in ids]


def create_draft(
    *,
    to: str,
    subject: str,
    body: str,
    in_reply_to_thread_id: Optional[str] = None,
    label: Optional[str] = None,
) -> str:
    """Create a Gmail draft. Returns the draft ID. If `label` is set, applies it
    so the EA can route to a `_test` label until go-live (spec §16 step 13)."""
    svc = _service()
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = PERRY_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    draft_body: dict = {"message": {"raw": raw}}
    if in_reply_to_thread_id:
        draft_body["message"]["threadId"] = in_reply_to_thread_id
    if label:
        draft_body["message"]["labelIds"] = [_resolve_label_id(svc, label)]

    resp = svc.users().drafts().create(userId="me", body=draft_body).execute()
    return resp["id"]


def _resolve_label_id(svc, name: str) -> str:
    """Find or create a Gmail label by name."""
    labels = svc.users().labels().list(userId="me").execute().get("labels", [])
    for l in labels:
        if l["name"] == name:
            return l["id"]
    created = svc.users().labels().create(
        userId="me",
        body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
    ).execute()
    return created["id"]


def fetch_brief_replies(
    *,
    brief_subject_prefix: str = "Big Brother Brief",
    hours: int = 24,
) -> list[GmailMessage]:
    """Find recent replies to the daily brief. Returns the latest reply per thread."""
    svc = _service()
    query = (
        f'from:{PERRY_EMAIL} '
        f'subject:"Re: {brief_subject_prefix}" newer_than:{max(1, hours // 24)}d'
    )
    ids = list_threads(query, max_results=20)
    out: list[GmailMessage] = []
    for tid in ids:
        thread = get_thread(tid)
        # Last message from Perry only.
        perrys = [m for m in thread.messages if m.sender_email.lower() == PERRY_EMAIL]
        if perrys:
            out.append(max(perrys, key=lambda m: m.received_at))
    return out
