"""One-shot: reassign every Marcus-owned deal to Perry.

Marcus has been removed from the team. All deals tagged `[marcus]` in the deal
name get retagged `[perry_patraszewski]`. The dashboard reads the rep from the
deal-name suffix, so renaming is sufficient — no HubSpot owner_id reshuffle.

Run locally with the .env loaded:
    python scripts/reassign_marcus_to_perry.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from tools import hubspot_client as hs

OLD_TAG = "[marcus]"
NEW_TAG = "[perry_patraszewski]"


def main() -> None:
    deals = hs.get_all_deals()
    print(f"Scanning {len(deals)} deals for {OLD_TAG} suffix...")

    to_update = []
    for d in deals:
        props = d.get("properties") or {}
        name = props.get("dealname") or ""
        if OLD_TAG in name:
            to_update.append((d["id"], name, name.replace(OLD_TAG, NEW_TAG)))

    print(f"Found {len(to_update)} deals to retag.")
    if not to_update:
        return

    client = hs._get_client()
    for deal_id, old_name, new_name in to_update:
        client.crm.deals.basic_api.update(
            deal_id=deal_id,
            simple_public_object_input={"properties": {"dealname": new_name}},
        )
        print(f"  ✓ {deal_id}: {old_name!r} → {new_name!r}")

    print(f"\nDone. Reassigned {len(to_update)} deals from Marcus to Perry.")


if __name__ == "__main__":
    main()
