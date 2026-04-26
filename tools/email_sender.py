"""Send HTML emails via Resend."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import List, Optional

import resend

from config.settings import RESEND_API_KEY, REPORT_FROM_EMAIL


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
