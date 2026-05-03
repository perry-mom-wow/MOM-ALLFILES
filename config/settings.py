from __future__ import annotations

import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"


def _secret(key: str, default: str = "") -> str:
    """Look up a secret from Streamlit Cloud's st.secrets first, then env, then default.

    Streamlit Cloud injects st.secrets at runtime; locally we fall back to .env.
    Importing streamlit lazily so this module also works in non-Streamlit contexts (CLI, cron).
    """
    try:
        import streamlit as st  # type: ignore
        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return os.getenv(key, default).strip()


def _load_yaml(filename: str) -> dict:
    path = CONFIG_DIR / filename
    with open(path) as f:
        return yaml.safe_load(f)


def load_reps() -> list[dict]:
    data = _load_yaml("reps.yaml")
    return [r for r in data["reps"] if r.get("active", True)]


def save_reps(reps: list[dict]) -> None:
    path = CONFIG_DIR / "reps.yaml"
    with open(path, "w") as f:
        yaml.dump({"reps": reps}, f, default_flow_style=False, allow_unicode=True)


def load_icp() -> dict:
    return _load_yaml("icp.yaml")


def get_rep_by_id(rep_id: str):
    return next((r for r in load_reps() if r["id"] == rep_id), None)


ANTHROPIC_API_KEY = _secret("ANTHROPIC_API_KEY")
TAVILY_API_KEY = _secret("TAVILY_API_KEY")
GOOGLE_MAPS_API_KEY = _secret("GOOGLE_MAPS_API_KEY")
HUBSPOT_API_KEY = _secret("HUBSPOT_API_KEY")
HUBSPOT_PIPELINE_ID = _secret("HUBSPOT_PIPELINE_ID", "default")
HUNTER_API_KEY = _secret("HUNTER_API_KEY")
SENDGRID_API_KEY = _secret("SENDGRID_API_KEY")
RESEND_API_KEY = _secret("RESEND_API_KEY")
REPORT_FROM_EMAIL = _secret("REPORT_FROM_EMAIL", "reports@mom-wow.com")
REPORT_RECIPIENTS = [
    e.strip() for e in _secret("REPORT_RECIPIENTS", "perry@mom-wow.com").split(",")
]
# Public product-portfolio URL appended as a P.S. to cold emails. Empty = suppressed.
PORTFOLIO_URL = _secret("PORTFOLIO_URL", "")
