"""Send HTML emails via Resend."""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import List, Optional

import resend

from config.settings import RESEND_API_KEY, REPORT_FROM_EMAIL


def send_outreach_email(
    to_email: str,
    subject: str,
    body_text: str,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> dict:
    """
    Send an outreach email (cold or follow-up) via Resend.
    Returns {sent: bool, id: str | None, error: str | None}.
    Honors AUTO_EMAIL_ENABLED env var — if "false" / "0", returns sent=False without sending.
    """
    if os.getenv("AUTO_EMAIL_ENABLED", "false").lower() not in ("true", "1", "yes"):
        return {"sent": False, "id": None, "error": "AUTO_EMAIL_ENABLED is off"}
    if not RESEND_API_KEY or not to_email:
        return {"sent": False, "id": None, "error": "no API key or no recipient"}

    resend.api_key = RESEND_API_KEY
    sender = from_email or REPORT_FROM_EMAIL
    if from_name:
        sender = f"{from_name} <{sender}>"

    # Convert plain-text body to simple HTML (preserve line breaks)
    html_body = "<br>".join(line for line in body_text.split("\n"))

    params: dict = {
        "from": sender,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "text": body_text,
    }
    if reply_to:
        params["reply_to"] = reply_to

    try:
        result = resend.Emails.send(params)
        return {"sent": True, "id": result.get("id"), "error": None}
    except Exception as e:
        return {"sent": False, "id": None, "error": str(e)}


def send_report_email(
    to_emails: list[str],
    subject: str,
    html_body: str,
    attachments: Optional[List[Path]] = None,
) -> None:
    """Send an HTML email (with optional attachments) to a list of recipients."""
    resend.api_key = RESEND_API_KEY

    params: dict = {
        "from": REPORT_FROM_EMAIL,
        "to": to_emails,
        "subject": subject,
        "html": html_body,
    }

    if attachments:
        params["attachments"] = []
        for path in attachments:
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            params["attachments"].append({
                "filename": path.name,
                "content": data,
            })

    resend.Emails.send(params)
