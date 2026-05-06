"""Parent-group dedup: detect when a prospect belongs to a hospitality chain that
we already have a deal with in HubSpot.

Stops the discovery agent from cold-contacting (e.g.) a Pestana property when
we're already in conversation with Pestana Group.

Matching uses three signals on the prospect:
  - venue name + description + contact_title  (substring against `aliases`)
  - venue website                               (domain suffix against `domains`)

If a group is matched, we then check HubSpot for an existing non-Lost deal whose
name contains the group name. If found, the prospect is flagged as a duplicate.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml


_CONFIG_PATH = Path(__file__).parent.parent / "config" / "parent_groups.yaml"


@lru_cache(maxsize=1)
def load_parent_groups() -> list[dict]:
    """Load and cache the parent-groups list."""
    if not _CONFIG_PATH.exists():
        return []
    with open(_CONFIG_PATH) as f:
        data = yaml.safe_load(f) or {}
    return data.get("parent_groups", []) or []


def _domain_of(url: str) -> str:
    """Extract a lowercase domain (no scheme, no path, no www) from a URL."""
    if not url:
        return ""
    m = re.match(r"^(?:https?://)?(?:www\.)?([^/\s?#]+)", url.strip(), re.IGNORECASE)
    return (m.group(1) if m else url).lower()


def match_parent_group(
    name: Optional[str] = None,
    website: Optional[str] = None,
    description: Optional[str] = None,
    contact_title: Optional[str] = None,
) -> Optional[dict]:
    """Return the parent-group dict if this prospect matches one, else None.

    Aliases are checked as case-insensitive substrings of (name + description + contact_title).
    Domains are checked as suffixes of the venue website's domain.
    """
    haystack = " ".join(s or "" for s in (name, description, contact_title)).lower()
    venue_domain = _domain_of(website or "")

    for group in load_parent_groups():
        # Domain match (strongest signal)
        if venue_domain:
            for d in group.get("domains", []) or []:
                d_norm = (d or "").lower().lstrip(".")
                if not d_norm:
                    continue
                if venue_domain == d_norm or venue_domain.endswith("." + d_norm):
                    return group

        # Alias substring match — use word boundaries to avoid spurious hits
        # (e.g. "ibis" shouldn't match "ibisco" — though our list has " " around aliases like "melia ").
        for alias in group.get("aliases", []) or []:
            alias_norm = (alias or "").strip().lower()
            if not alias_norm:
                continue
            # Word boundary on the start; allow trailing word-boundary OR end of string
            pattern = r"(?<![\w])" + re.escape(alias_norm) + r"(?![\w])"
            if re.search(pattern, haystack):
                return group
    return None


def verify_contact_for_venue(
    venue_name: str,
    contact_title: Optional[str],
) -> tuple[bool, str]:
    """Heuristic check: does this contact's title plausibly refer to this venue?

    Returns (ok, reason).
        ok=True   → contact looks fine OR we can't tell. Don't block.
        ok=False  → contact's title strongly indicates a DIFFERENT employer (a known
                    hospitality chain) that doesn't match this venue. Drop the contact.

    Detection rule: if the contact's title contains a parent-group alias for which the
    venue is NOT also a member, we treat that as a current-employer mismatch. Useful
    for catching cases like Paulo Bicho ("F&B Director @ Hyatt Regency Vilamoura")
    being scraped as the contact for "Hotel Dom Pedro Lisboa".

    Conservative by design — only fires when there's a clear chain signal in the title.
    """
    if not contact_title or not venue_name:
        return True, ""

    # Which group does the venue belong to (if any)?
    venue_group = match_parent_group(name=venue_name)

    # Which group does the contact's title point at?
    title_group = match_parent_group(contact_title=contact_title)

    if title_group is None:
        return True, ""  # No chain signal in title — can't tell, don't block

    # Title cites an employer; if it's the same group as the venue, fine.
    if venue_group and venue_group.get("name") == title_group.get("name"):
        return True, ""

    # Title cites an employer that doesn't match the venue's group → mismatch.
    return False, (
        f"contact title points at {title_group['name']} but venue '{venue_name}' "
        f"is not part of that group"
    )


def find_existing_group_deal(group_name: str) -> Optional[dict]:
    """Search HubSpot for a non-Lost deal whose name contains this parent-group name.

    Imports HubSpot client lazily so this module stays usable in unit tests.
    Returns {'id', 'properties'} or None.
    """
    if not group_name:
        return None
    # Lazy import to avoid pulling in the SDK / settings during test collection.
    from tools import hubspot_client as hs  # type: ignore

    try:
        client = hs._get_client()
    except Exception:
        return None

    # Search by the most distinctive token of the group name (first word, len>=4 to
    # avoid noise tokens like "the", "and"). Falls back to full name otherwise.
    tokens = [t for t in re.split(r"\s+", group_name) if len(t) >= 4]
    needle = tokens[0] if tokens else group_name

    try:
        results = client.crm.deals.search_api.do_search(
            public_object_search_request={
                "filterGroups": [{
                    "filters": [
                        {"propertyName": "dealname", "operator": "CONTAINS_TOKEN", "value": needle},
                    ],
                }],
                "properties": ["dealname", "dealstage"],
                "limit": 10,
            }
        )
    except Exception:
        return None

    if not results.results:
        return None

    needle_l = needle.lower()
    for r in results.results:
        deal_name = (r.properties.get("dealname") or "").lower()
        if needle_l not in deal_name:
            continue
        # Skip Lost deals — we can re-engage them under a parent group again later.
        stage = (r.properties.get("dealstage") or "").lower()
        if stage in {"closedlost", "lost"}:
            continue
        return {"id": r.id, "properties": r.properties}
    return None
