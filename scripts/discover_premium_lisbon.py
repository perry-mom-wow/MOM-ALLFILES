"""Discover premium hotels (4-5 star) + bars in Lisbon, split between Marcus and Laura."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Clear inherited Anthropic vars so .env values win, then load .env with override.
for _var in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
    os.environ.pop(_var, None)
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=True)

from agents.discovery import discover_prospects  # noqa: E402
from agents.researcher import research_prospect  # noqa: E402
from agents.writer import generate_sequence  # noqa: E402
from agents.crm import onboard_prospect, GatekeeperRejection, DuplicateInCRM  # noqa: E402


LOCATION = "Lisboa, Portugal"

# Premium-focused queries — 4 and 5 star hotels only.
QUERY_OVERRIDES = {
    "hotel": [
        "5 star hotel",
        "luxury hotel",
        "4 star boutique hotel",
    ],
    "bar": [
        "cocktail bar",
        "rooftop bar",
        "wine bar",
    ],
}

# Already-in-touch — substring match, case-insensitive.
EXCLUDE = [
    "Pestana",
    "Standard Hotel",
    "Sublime",
    "Aetios",
]

MAX_PER_TYPE = 15

# Tier-based rep assignment:
#   Tier 1 → Marcus only
#   Tier 2 → split Marcus/Laura
#   Tier 3 → Laura only
_tier2_toggle = {"next": "marcus"}


def assign_rep(tier: int) -> str:
    if tier == 1:
        return "marcus"
    if tier == 3:
        return "laura"
    # Tier 2: alternate
    rep = _tier2_toggle["next"]
    _tier2_toggle["next"] = "laura" if rep == "marcus" else "marcus"
    return rep


def main() -> None:
    print(f"\n🔍 Discovering premium hotels + bars in {LOCATION}")
    print(f"   Excluding: {', '.join(EXCLUDE)}\n")

    prospects = discover_prospects(
        location=LOCATION,
        venue_types=["bar", "hotel"],
        max_per_type=MAX_PER_TYPE,
        exclude_names=EXCLUDE,
        query_overrides=QUERY_OVERRIDES,
    )
    print(f"\n✅ Found {len(prospects)} prospects after filtering.\n")

    summary = {"marcus": 0, "laura": 0, "errors": 0, "duplicates": 0, "rejected": 0}

    for i, raw in enumerate(prospects, 1):
        print(f"\n[{i}/{len(prospects)}] {raw.name}")
        try:
            profile = research_prospect(raw)
            rep_id = assign_rep(profile.tier)
            print(f"    ✓ Tier {profile.tier} · {profile.contact_name or '?'} ({profile.contact_title or '?'})  →  {rep_id}")
            sequence = generate_sequence(profile, rep_id)
            try:
                result = onboard_prospect(profile, sequence, rep_id)
                summary[rep_id] += 1
                print(f"    ✅ Deal {result['deal_id']} · €{result['revenue_potential_eur']}/mo")
            except DuplicateInCRM as dup:
                summary["duplicates"] += 1
                print(f"    🔄 Duplicate: {dup}")
            except GatekeeperRejection as gk:
                summary["rejected"] += 1
                print(f"    🚫 Rejected: {gk}")
        except Exception as e:
            summary["errors"] += 1
            print(f"    ❌ Error: {e}")

    print("\n" + "=" * 60)
    print(f"Marcus onboarded: {summary['marcus']}")
    print(f"Laura onboarded:  {summary['laura']}")
    print(f"Duplicates:       {summary['duplicates']}")
    print(f"Rejected:         {summary['rejected']}")
    print(f"Errors:           {summary['errors']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
