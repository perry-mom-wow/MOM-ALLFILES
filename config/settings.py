from __future__ import annotations

import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"


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


ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY", "")
HUBSPOT_PIPELINE_ID = os.getenv("HUBSPOT_PIPELINE_ID", "default")
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
REPORT_FROM_EMAIL = os.getenv("REPORT_FROM_EMAIL", "reports@mom-wow.com")
REPORT_RECIPIENTS = [
    e.strip() for e in os.getenv("REPORT_RECIPIENTS", "perry@mom-wow.com").split(",")
]
