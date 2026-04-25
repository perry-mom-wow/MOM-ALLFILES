"""Send HTML emails via SendGrid."""
from __future__ import annotations

import base64
from pathlib import Path

import sendgrid
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition

from config.settings import SENDGRID_API_KEY, REPORT_FROM_EMAIL


def send_report_email(
    to_emails: list[str],
    subject: str,
    html_body: str,
    attachments: list[Path] | None = None,
) -> None:
    sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
    message = Mail(
        from_email=REPORT_FROM_EMAIL,
        to_emails=to_emails,
        subject=subject,
        html_content=html_body,
    )

    if attachments:
        for path in attachments:
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            ext = path.suffix.lstrip(".")
            mime = "image/png" if ext == "png" else "application/octet-stream"
            attachment = Attachment(
                file_content=FileContent(data),
                file_name=FileName(path.name),
                file_type=FileType(mime),
                disposition=Disposition("inline"),
            )
            message.add_attachment(attachment)

    sg.send(message)
