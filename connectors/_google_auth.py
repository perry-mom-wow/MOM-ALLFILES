"""Shared Google OAuth helper for Gmail / Calendar / Drive / Sheets.

Reads a refresh token from env (GOOGLE_REFRESH_TOKEN) plus client_id/secret
(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET). On first-time setup, run
`python -m connectors._google_auth setup` to walk through the OAuth flow and
print a refresh token to paste into .env.

Single Credentials object is reused across Gmail/Calendar/Sheets to keep one
token-refresh cycle.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# All Google scopes the EA needs across services.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

_TOKEN_CACHE_PATH = Path(__file__).parent.parent / "data" / ".google_token.json"
_credentials = None


class GoogleAuthMissing(RuntimeError):
    """Raised when Google OAuth env vars are not configured."""


def get_credentials():
    """Return a refreshed google.oauth2.credentials.Credentials, or raise."""
    global _credentials
    if _credentials is not None and _credentials.valid:
        return _credentials

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError as e:
        raise GoogleAuthMissing(
            "google-auth not installed. Run: pip install google-auth google-auth-oauthlib google-api-python-client"
        ) from e

    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN", "").strip()

    if not (client_id and client_secret and refresh_token):
        raise GoogleAuthMissing(
            "Google OAuth not configured. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, "
            "GOOGLE_REFRESH_TOKEN in .env. To bootstrap, run "
            "`python -m connectors._google_auth setup`."
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    _credentials = creds
    return creds


def setup_flow_cli() -> None:
    """One-shot installed-app OAuth flow. Prints refresh token to copy into .env."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Install google-auth-oauthlib: pip install google-auth-oauthlib")
        sys.exit(1)

    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if not (client_id and client_secret):
        print("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET first.")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    print("\n=== Paste this into your .env ===")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("=================================\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_flow_cli()
    else:
        print("Usage: python -m connectors._google_auth setup")
