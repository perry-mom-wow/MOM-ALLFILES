"""HubSpot CRM client — contacts, companies, deals, activities."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

import hubspot
from hubspot.crm.contacts import SimplePublicObjectInputForCreate as ContactInput
from hubspot.crm.companies import SimplePublicObjectInputForCreate as CompanyInput
from hubspot.crm.deals import SimplePublicObjectInputForCreate as DealInput
from hubspot.crm.associations.v4 import AssociationSpec

from config.settings import HUBSPOT_API_KEY, HUBSPOT_PIPELINE_ID, load_icp

_client: Optional[hubspot.Client] = None


def _get_client() -> hubspot.Client:
    global _client
    if _client is None:
        _client = hubspot.Client.create(access_token=HUBSPOT_API_KEY)
    return _client


ICP = load_icp()
STAGE_MAP = {s["id"]: s["label"] for s in ICP["pipeline_stages"]}

# Per-tier HubSpot pipeline IDs in the new portal (147910665).
# Tier 1 → "Tier 1 - Marcus" pipeline (HubSpot's default-pipeline shell).
# Tier 2 + 3 → "Tier 2/3 - Laura" pipeline.
PIPELINE_TIER_1 = "default"
PIPELINE_TIER_23 = "3713146104"

# Map our internal stage names → HubSpot stage IDs, per pipeline.
# Both pipelines have the same labels but different stage IDs.
STAGE_MAP_BY_PIPELINE = {
    PIPELINE_TIER_1: {
        "prospect":       "appointmentscheduled",
        "contacted":      "qualifiedtobuy",
        "replied":        "presentationscheduled",
        "tasting_booked": "contractsent",
        "nurture":        "4961801429",
        "won":            "closedwon",
        "lost":           "closedlost",
        "active_client":  "4961801428",
    },
    PIPELINE_TIER_23: {
        "prospect":       "5143548138",
        "contacted":      "5143548139",
        "replied":        "5143548140",
        "tasting_booked": "5143548142",
        "nurture":        "5143548146",
        "won":            "5143548143",
        "lost":           "5143548144",
        "active_client":  "5143548145",
    },
}


def pipeline_for_tier(tier: int) -> str:
    return PIPELINE_TIER_1 if int(tier) == 1 else PIPELINE_TIER_23


def _stage_id(pipeline: str, our_stage: str) -> str:
    return STAGE_MAP_BY_PIPELINE.get(pipeline, {}).get(our_stage, our_stage)


# Backwards-compat shim — assumes tier-1 pipeline; only used by legacy calls.
def _hs_stage(stage: str) -> str:
    return _stage_id(PIPELINE_TIER_1, stage)


# ── Companies ──────────────────────────────────────────────────────────────────

def upsert_company(name: str, website: Optional[str], domain: Optional[str]) -> str:
    """Create or find a company by name. Returns HubSpot company ID."""
    client = _get_client()
    props = {"name": name}
    if website:
        props["website"] = website
    if domain:
        props["domain"] = domain

    # Try to find existing
    results = client.crm.companies.search_api.do_search(
        public_object_search_request={
            "filterGroups": [{"filters": [{"propertyName": "name", "operator": "EQ", "value": name}]}],
            "limit": 1,
        }
    )
    if results.results:
        return results.results[0].id

    resp = client.crm.companies.basic_api.create(
        simple_public_object_input_for_create=CompanyInput(properties=props)
    )
    return resp.id


# ── Contacts ───────────────────────────────────────────────────────────────────

def upsert_contact(
    email: Optional[str],
    first_name: str,
    last_name: str,
    company_id: str,
    linkedin_url: Optional[str] = None,
    instagram_handle: Optional[str] = None,
    phone: Optional[str] = None,
) -> str:
    """Create or find a contact. Returns HubSpot contact ID."""
    client = _get_client()
    props: dict[str, Any] = {
        "firstname": first_name,
        "lastname": last_name,
    }
    if email:
        props["email"] = email
    if linkedin_url:
        props["linkedin_bio"] = linkedin_url
    if instagram_handle:
        props["twitterhandle"] = instagram_handle
    if phone:
        props["phone"] = phone

    if email:
        results = client.crm.contacts.search_api.do_search(
            public_object_search_request={
                "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
                "limit": 1,
            }
        )
        if results.results:
            contact_id = results.results[0].id
            # Associate with company
            _associate(client, "contact", contact_id, "company", company_id, 280)
            return contact_id

    resp = _create_contact_tolerant(client, props)
    contact_id = resp.id
    _associate(client, "contact", contact_id, "company", company_id, 280)
    return contact_id


def _create_contact_tolerant(client, props: dict[str, Any]):
    """Create a contact, retrying without any properties HubSpot says don't exist."""
    while True:
        try:
            return client.crm.contacts.basic_api.create(
                simple_public_object_input_for_create=ContactInput(properties=props)
            )
        except Exception as e:
            haystack = str(getattr(e, "body", "") or "") + " " + str(e)
            missing = re.findall(r'Property\s+\\?"([^"\\]+)\\?"\s+does not exist', haystack)
            if not missing or not any(m in props for m in missing):
                raise
            for m in missing:
                props.pop(m, None)
            print(f"      ⚠️  HubSpot portal missing properties — dropped: {missing}; retrying.")


def _associate(client, from_type, from_id, to_type, to_id, association_type_id):
    try:
        client.crm.associations.v4.basic_api.create(
            object_type=from_type,
            object_id=from_id,
            to_object_type=to_type,
            to_object_id=to_id,
            association_spec=[AssociationSpec(
                association_category="HUBSPOT_DEFINED",
                association_type_id=association_type_id,
            )],
        )
    except Exception:
        pass


# ── Deals ──────────────────────────────────────────────────────────────────────

def create_deal(
    name: str,
    company_id: str,
    contact_id: str,
    stage: str,
    rep_id: str,
    tier: int,
    venue_type: str,
    revenue_potential_eur: float,
    next_followup_date: Optional[date] = None,
) -> str:
    """Create a deal in the pipeline. Returns deal ID."""
    client = _get_client()
    pipeline_id = pipeline_for_tier(tier)
    props: dict[str, Any] = {
        "dealname": f"{name} [{rep_id}]",
        "pipeline": pipeline_id,
        "dealstage": _stage_id(pipeline_id, stage),
        "amount": str(revenue_potential_eur),
    }
    if next_followup_date:
        props["closedate"] = next_followup_date.isoformat()

    resp = client.crm.deals.basic_api.create(
        simple_public_object_input_for_create=DealInput(properties=props)
    )
    deal_id = resp.id
    _associate(client, "deal", deal_id, "company", company_id, 341)
    if contact_id:
        _associate(client, "deal", deal_id, "contact", contact_id, 3)
    return deal_id


def delete_deal(deal_id: str) -> None:
    """Archive (soft-delete) a deal in HubSpot."""
    _get_client().crm.deals.basic_api.archive(deal_id=deal_id)


def delete_company(company_id: str) -> None:
    _get_client().crm.companies.basic_api.archive(company_id=company_id)


def delete_contact(contact_id: str) -> None:
    _get_client().crm.contacts.basic_api.archive(contact_id=contact_id)


def find_existing_deal_by_venue(venue_name: str) -> Optional[dict]:
    """
    Search HubSpot for an existing deal that matches this venue name (any rep, any stage
    except Lost). Returns the deal dict (with id + properties) or None.
    """
    if not venue_name:
        return None
    client = _get_client()
    try:
        results = client.crm.deals.search_api.do_search(
            public_object_search_request={
                "filterGroups": [{
                    "filters": [
                        {"propertyName": "dealname", "operator": "CONTAINS_TOKEN", "value": venue_name},
                    ],
                }],
                "properties": ["dealname", "dealstage", "hubspot_owner_id", "amount"],
                "limit": 10,
            }
        )
    except Exception:
        return None

    if not results.results:
        return None

    needle = venue_name.lower().strip()
    for r in results.results:
        name = (r.properties.get("dealname") or "").lower()
        # Strip our suffixes for fair comparison
        name_clean = re.sub(r"\s*[—·]\s*(mom-wow|MOM).*$", "", name).strip()
        if name_clean == needle or needle in name_clean:
            stage = (r.properties.get("dealstage") or "").lower()
            if stage != "closedlost":
                return {"id": r.id, "properties": r.properties}
    return None


def get_deal_associations(deal_id: str) -> dict:
    """Return {'companies': [ids], 'contacts': [ids]} for a deal."""
    client = _get_client()
    out = {"companies": [], "contacts": []}
    for kind in ("companies", "contacts"):
        try:
            resp = client.crm.associations.v4.basic_api.get_page(
                object_type="deal",
                object_id=deal_id,
                to_object_type=kind[:-1],  # 'company' / 'contact'
            )
            out[kind] = [r.to_object_id for r in resp.results]
        except Exception:
            pass
    return out


def update_deal_stage(deal_id: str, stage: str) -> None:
    """Update a deal's stage. Looks up the deal's pipeline first so we pick the right stage ID."""
    client = _get_client()
    deal = client.crm.deals.basic_api.get_by_id(deal_id=deal_id, properties=["pipeline"])
    pipeline_id = (deal.properties or {}).get("pipeline") or PIPELINE_TIER_1
    client.crm.deals.basic_api.update(
        deal_id=deal_id,
        simple_public_object_input={"properties": {"dealstage": _stage_id(pipeline_id, stage)}},
    )


def update_deal_followup(deal_id: str, next_date: date, re_engagement_count: Optional[int] = None) -> None:
    client = _get_client()
    props = {"closedate": next_date.isoformat()}
    if re_engagement_count is not None:
        props["hs_deal_stage_probability"] = str(re_engagement_count)
    client.crm.deals.basic_api.update(
        deal_id=deal_id,
        simple_public_object_input={"properties": props},
    )


# ── Activities / Notes ─────────────────────────────────────────────────────────

def log_note(contact_id: Optional[str], deal_id: str, body: str) -> None:
    """Log a note on a deal (and contact, if provided) using the modern v3 Notes API."""
    client = _get_client()
    timestamp_ms = int(datetime.utcnow().timestamp() * 1000)

    # Create the note (using generic objects API for "notes" object type)
    try:
        resp = client.crm.objects.basic_api.create(
            object_type="notes",
            simple_public_object_input_for_create={
                "properties": {
                    "hs_note_body": body,
                    "hs_timestamp": str(timestamp_ms),
                }
            },
        )
        note_id = resp.id
    except Exception:
        return  # silently skip if note creation fails — non-critical

    # Associate the note to the deal always; to the contact only if provided
    _associate(client, "notes", note_id, "deal", deal_id, 214)
    if contact_id:
        _associate(client, "notes", note_id, "contact", contact_id, 202)


# ── Reporting queries ──────────────────────────────────────────────────────────

def get_all_deals() -> list[dict]:
    """Fetch all deals with key properties."""
    client = _get_client()
    props = [
        "dealname", "dealstage", "amount", "hubspot_owner_id",
        "closedate", "createdate", "hs_lastmodifieddate",
    ]
    results = []
    after = None
    while True:
        kwargs: dict = {"limit": 100, "properties": props}
        if after:
            kwargs["after"] = after
        page = client.crm.deals.basic_api.get_page(**kwargs)
        results.extend([d.to_dict() for d in page.results])
        if not page.paging or not page.paging.next:
            break
        after = page.paging.next.after
    return results


def get_deals_needing_followup(today: date) -> list[dict]:
    """Return deals where closedate (next follow-up) <= today and not Won/Lost."""
    all_deals = get_all_deals()
    terminal = {"won", "lost"}
    due = []
    for d in all_deals:
        props = d.get("properties", {})
        stage = (props.get("dealstage") or "").lower()
        if stage in terminal:
            continue
        close_str = props.get("closedate")
        if close_str:
            close_date = date.fromisoformat(close_str[:10])
            if close_date <= today:
                due.append(d)
    return due
