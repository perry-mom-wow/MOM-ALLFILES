"""Onboard a curated list of Lisbon Tier 1 luxury venues for Irina.

Pipeline per venue: discover_named_venues (Tavily lookup + website scrape)
→ research_prospect (Claude) → generate_sequence (Claude, Irina's voice/PT)
→ onboard_prospect (HubSpot company/contact/deal + queue + sequence file).

Dedupe, parent-group, and gatekeeper rejections are expected for a few —
that's the system protecting us from double-contacting accounts. The list
is deliberately longer than the target so we land 17+ net-new.

Usage:
    python scripts/onboard_tier1_irina.py --execute [--batch-start N --batch-size 5]
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Curated Lisbon Tier 1 list. Excludes brands already in HubSpot
# (Memmo, Valverde, Four Seasons, InterContinental, Sheraton, Altis,
# Lumiares, Santiago de Alfama, Hilton, VIP Grand, EPIC SANA, Convent
# Square, Pestana properties).
TIER1_VENUES = [
    "Bairro Alto Hotel",
    "Tivoli Avenida Liberdade Lisboa",
    "Corinthia Lisbon",
    "Sofitel Lisbon Liberdade",
    "Pousada de Lisboa",
    "Verride Palacio Santa Catarina",
    "Santa Clara 1728",
    "The One Palacio da Anunciada",
    "Olissippo Lapa Palace",
    "Hotel Avenida Palace",
    "PortoBay Liberdade",
    "The Ivens Hotel",
    "Martinhal Lisbon Chiado",
    "Palacio do Governador",
    "Iberostar Selection Lisboa",
    "Hotel Britania Art Deco",
    "Heritage Avenida Liberdade",
    "Hyatt Regency Lisboa",
    "Torel Palace Lisbon",
    "Wine & Books Lisboa Hotel",
    "The Vintage Lisbon",
    "The Editory Riverside Santa Apolonia",
    "Sublime Lisboa",
    "Myriad by SANA Hotels",
]

REP_ID = "irina"


def main(execute: bool, batch_start: int, batch_size: int) -> int:
    names = TIER1_VENUES[batch_start:batch_start + batch_size]
    if not names:
        print("Batch empty — done.")
        return 0
    if not execute:
        print(f"DRY-RUN. Would onboard {len(names)} venues for {REP_ID}:")
        for n in names:
            print(f"  • {n}")
        return 0

    from agents.discovery import discover_named_venues
    from agents.researcher import research_prospect
    from agents.writer import generate_sequence
    from agents.crm import (
        onboard_prospect, GatekeeperRejection, DuplicateInCRM, ParentGroupConflict,
    )

    counts = {"onboarded": 0, "duplicate": 0, "group_conflict": 0,
              "gatekeeper": 0, "error": 0}

    raws = discover_named_venues(names, location_hint="Lisboa, Portugal", venue_type="hotel")
    for raw in raws:
        raw.tier = 1  # curated Tier 1 list — override the seeded default
        prefix = f"  {raw.name[:38]:<38}"
        try:
            profile = research_prospect(raw)
            profile.tier = 1
            sequence = generate_sequence(profile, REP_ID)
            result = onboard_prospect(profile, sequence, REP_ID)
            counts["onboarded"] += 1
            print(f"{prefix}  ✓ onboarded (deal {result.get('deal_id')})", flush=True)
        except DuplicateInCRM as e:
            counts["duplicate"] += 1
            print(f"{prefix}  ⏭ duplicate: {e}", flush=True)
        except ParentGroupConflict as e:
            counts["group_conflict"] += 1
            print(f"{prefix}  ⏭ group conflict: {e}", flush=True)
        except GatekeeperRejection as e:
            counts["gatekeeper"] += 1
            print(f"{prefix}  ⏭ gatekeeper: {e}", flush=True)
        except Exception as e:
            counts["error"] += 1
            print(f"{prefix}  ✗ error: {e}", flush=True)
            traceback.print_exc()

    print(f"\nBatch summary: {counts}", flush=True)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true")
    p.add_argument("--batch-start", type=int, default=0)
    p.add_argument("--batch-size", type=int, default=6)
    args = p.parse_args()
    sys.exit(main(args.execute, args.batch_start, args.batch_size))
