"""Reset HubSpot deals back to 'Prospect' if their outreach is still queued (not sent)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Clear inherited Anthropic vars so .env wins.
for _var in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
    os.environ.pop(_var, None)
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=True)

from tools import hubspot_client as hs  # noqa: E402
from tools.outreach_queue import QUEUE_DIR  # noqa: E402
import json  # noqa: E402


def collect_queued_deal_ids() -> set[str]:
    """Find every deal_id that has at least one item still sitting in any queue file."""
    deal_ids: set[str] = set()
    for path in QUEUE_DIR.glob("*.json"):
        try:
            items = json.loads(path.read_text())
        except Exception:
            continue
        for item in items:
            did = item.get("deal_id")
            if did:
                deal_ids.add(str(did))
    return deal_ids


def main() -> None:
    queued = collect_queued_deal_ids()
    print(f"Found {len(queued)} unique deal IDs with pending queue items.\n")
    if not queued:
        print("Nothing to reset.")
        return

    reset = 0
    skipped = 0
    errors = 0
    for did in sorted(queued):
        try:
            client = hs._get_client()
            deal = client.crm.deals.basic_api.get_by_id(deal_id=did, properties=["dealname", "dealstage"])
            stage = (deal.properties.get("dealstage") or "").lower()
            name = deal.properties.get("dealname", "")
            # HubSpot stage "qualifiedtobuy" maps to our internal "contacted".
            if stage == "qualifiedtobuy":
                hs.update_deal_stage(did, "prospect")
                reset += 1
                print(f"  ✅ Reset {did} ({name})")
            else:
                skipped += 1
                print(f"  ⏭  Skip {did} ({name}) — stage '{stage}'")
        except Exception as e:
            errors += 1
            print(f"  ❌ {did}: {e}")

    print("\n" + "=" * 50)
    print(f"Reset to Prospect: {reset}")
    print(f"Skipped:           {skipped}")
    print(f"Errors:            {errors}")
    print("=" * 50)


if __name__ == "__main__":
    main()
