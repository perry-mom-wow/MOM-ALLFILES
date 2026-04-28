"""Cleanup agent: finds and deletes junk deals (article titles, wrong-country venues)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path regardless of how this module is loaded.
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Streamlit Cloud is configured to run this file as the main script.
# When that happens, hand off to the real dashboard instead of executing
# this module's setup code (which would just render a blank page).
if __name__ == "__main__":
    import runpy
    runpy.run_path(str(_ROOT / "dashboard" / "app.py"), run_name="__main__")
    sys.exit(0)

import anthropic

from config.settings import ANTHROPIC_API_KEY, load_icp
from tools import hubspot_client as hs

ICP = load_icp()
TARGET_COUNTRY = ICP.get("brand", {}).get("country", "Portugal")
_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


SYSTEM = """You audit a B2B CRM for a Portuguese juice brand.

For each deal name shown, decide is_junk = true if ANY of these apply:
- It's an article title or directory listing ("Best 30 restaurants...", "Welcome to...", "Restaurants in...")
- It's a contact/reservation page title ("Contact X - Reserve a Table at...")
- It's clearly a venue outside Portugal
- It's a generic page title without a clear individual venue name

Otherwise is_junk = false (keep it — it's a real Portuguese venue).

Respond with VALID JSON ONLY:
{"results": [{"i": 0, "is_junk": true, "reason": "article title"}, ...]}
"""


def find_junk_deals() -> list[dict]:
    """Pull all deals from HubSpot and use Claude to identify junk."""
    print("📥 Fetching all deals from HubSpot...")
    deals = hs.get_all_deals()
    print(f"   Found {len(deals)} total deals.")

    if not deals:
        return []

    # Strip the "[rep_id]" suffix and " — mom-wow" tag from names for cleaner judging
    items = []
    for i, d in enumerate(deals):
        name = (d.get("properties", {}).get("dealname") or "").strip()
        clean = re.sub(r"\s*[—·]\s*(mom-wow|MOM).*$", "", name).strip()
        clean = re.sub(r"\s*\[[^\]]+\]\s*$", "", clean).strip()
        items.append({"i": i, "name": clean, "id": d["id"]})

    print(f"🤖 Asking Claude to flag junk among {len(items)} deals...")
    try:
        response = _get_client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            system=[{
                "type": "text",
                "text": SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": json.dumps([{"i": it["i"], "name": it["name"]} for it in items]),
            }],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        decisions = {d["i"]: d for d in json.loads(text).get("results", [])}
    except Exception as e:
        print(f"   ❌ Claude filter failed: {e}")
        return []

    junk = []
    for it in items:
        d = decisions.get(it["i"], {})
        if d.get("is_junk"):
            junk.append({
                "deal_id": it["id"],
                "name": it["name"],
                "reason": d.get("reason", ""),
            })
    return junk


DEDUPE_SYSTEM = """You group deal names that refer to the SAME venue.

For each group of duplicates, return:
- "keep": the index of the cleanest, most accurate name (prefer short proper nouns over article titles)
- "delete": list of indices that are duplicates of the kept one and should be removed

Two names refer to the same venue if they describe the same physical place
(e.g. "Monte Mar Lisboa" and "Seafood Restaurant Lisbon - River View Dining & Event Venue"
are the same restaurant — Monte Mar serves seafood by the river in Lisbon).

Respond with VALID JSON ONLY:
{"groups": [{"keep": 2, "delete": [0, 5]}, ...]}
If there are no duplicates, return {"groups": []}.
"""


def find_duplicate_deals() -> list[dict]:
    """Return list of deal IDs that are duplicates of cleaner versions to keep."""
    deals = hs.get_all_deals()
    if not deals:
        return []

    items = []
    for i, d in enumerate(deals):
        name = (d.get("properties", {}).get("dealname") or "").strip()
        clean = re.sub(r"\s*[—·]\s*(mom-wow|MOM).*$", "", name).strip()
        clean = re.sub(r"\s*\[[^\]]+\]\s*$", "", clean).strip()
        created = d.get("properties", {}).get("createdate") or ""
        items.append({"i": i, "name": clean, "id": d["id"], "created": created})

    # ── Pass 1: deterministic exact-match dedupe (case-insensitive) ──
    by_name: dict[str, list[dict]] = {}
    for it in items:
        key = it["name"].lower().strip()
        by_name.setdefault(key, []).append(it)

    exact_dupes = []
    for key, group in by_name.items():
        if len(group) > 1:
            # Keep the OLDEST (preserves history); delete the rest
            group_sorted = sorted(group, key=lambda x: x.get("created", ""))
            keep = group_sorted[0]
            for dup in group_sorted[1:]:
                exact_dupes.append({
                    "deal_id": dup["id"],
                    "name": dup["name"],
                    "reason": f"exact-name duplicate of older deal '{keep['name']}'",
                })

    print(f"🤖 Asking Claude to dedupe {len(items)} deals...")
    try:
        response = _get_client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=[{"type": "text", "text": DEDUPE_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": json.dumps([{"i": it["i"], "name": it["name"]} for it in items])}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        groups = json.loads(text).get("groups", [])
    except Exception as e:
        print(f"   ❌ Dedupe failed: {e}")
        return []

    to_delete = list(exact_dupes)  # start with deterministic exact-match dupes
    seen_ids = {d["deal_id"] for d in to_delete}
    for g in groups:
        keep_idx = g.get("keep")
        for del_idx in g.get("delete", []):
            keep_name = items[keep_idx]["name"] if keep_idx is not None and keep_idx < len(items) else "?"
            if del_idx < len(items) and items[del_idx]["id"] not in seen_ids:
                to_delete.append({
                    "deal_id": items[del_idx]["id"],
                    "name": items[del_idx]["name"],
                    "reason": f"duplicate of '{keep_name}'",
                })
                seen_ids.add(items[del_idx]["id"])
    return to_delete


def cleanup(dry_run: bool = True) -> dict:
    """Delete junk + duplicate deals (and their linked companies/contacts)."""
    junk = find_junk_deals()
    duplicates = find_duplicate_deals()
    # Combine, dedupe by deal_id (in case a dupe was also flagged as junk)
    seen = set()
    combined = []
    for item in junk + duplicates:
        if item["deal_id"] not in seen:
            seen.add(item["deal_id"])
            combined.append(item)
    junk = combined
    if not junk:
        print("✅ No junk or duplicates found.")
        return {"deleted": 0, "junk": []}

    print(f"\n🧹 Identified {len(junk)} junk deals:")
    for j in junk:
        print(f"   • {j['name']}  —  {j['reason']}")

    if dry_run:
        print("\n(Dry-run — nothing deleted. Re-run with --apply to actually delete.)")
        return {"deleted": 0, "junk": junk}

    print(f"\n🗑  Deleting {len(junk)} deals + their linked companies & contacts...")
    deleted = 0
    for j in junk:
        try:
            assoc = hs.get_deal_associations(j["deal_id"])
            hs.delete_deal(j["deal_id"])
            for cid in assoc.get("companies", []):
                try: hs.delete_company(cid)
                except Exception: pass
            for cid in assoc.get("contacts", []):
                try: hs.delete_contact(cid)
                except Exception: pass
            deleted += 1
            print(f"   ✓ Deleted: {j['name']}")
        except Exception as e:
            print(f"   ✗ Failed to delete {j['name']}: {e}")

    print(f"\n✅ Cleanup complete — deleted {deleted}/{len(junk)} junk deals.")
    return {"deleted": deleted, "junk": junk}
