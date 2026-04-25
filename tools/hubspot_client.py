"""HubSpot CRM client — contacts, companies, deals, activities."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import hubspot
from hubspot.crm.contacts import SimplePublicObjectInputForCreate as ContactInput
from hubspot.crm.companies import SimplePublicObjectInputForCreate as CompanyInput
from hubspot.crm.deals import SimplePublicObjectInputForCreate as DealInput
from hubspot.crm.associations.v4 import AssociationSpec

from config.settings import HUBSPOT_API_KEY, HUBSPOT_PIPELINE_ID, load_icp

_client: hubspot.Client | None = None


def _get_client() -> hubspot.Client:
    global _client
    if _client is None:
        _client = hubspot.Client.create(access_token=HUBSPOT_API_KEY)
    return _client


ICP = load_icp()
STAGE_MAP = {s["id"]: s["label"] for s in ICP["pipeline_stages"]}


# ── Companies ──────────────────────────────────────────────────────────────────

def upsert_company(name: str, website: str | None, domain: str | None) -> str:
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
    email: str | None,
    first_name: str,
    last_name: str,
    company_id: str,
    linkedin_url: str | None = None,
    instagram_handle: str | None = None,
    phone: str | None = None,
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

    resp = client.crm.contacts.basic_api.create(
        simple_public_object_input_for_create=ContactInput(properties=props)
    )
    contact_id = resp.id
    _associate(client, "contact", contact_id, "company", company_id, 280)
    return contact_id


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
    next_followup_date: date | None = None,
) -> str:
    """Create a deal in the pipeline. Returns deal ID."""
    client = _get_client()
    props: dict[str, Any] = {
        "dealname": name,
        "pipeline": HUBSPOT_PIPELINE_ID,
        "dealstage": stage,
        "hubspot_owner_id": rep_id,
        "amount": str(revenue_potential_eur),
        "deal_currency_code": "EUR",
    }
    if next_followup_date:
        props["closedate"] = next_followup_date.isoformat()

    resp = client.crm.deals.basic_api.create(
        simple_public_object_input_for_create=DealInput(properties=props)
    )
    deal_id = resp.id
    _associate(client, "deal", deal_id, "company", company_id, 341)
    _associate(client, "deal", deal_id, "contact", contact_id, 3)
    return deal_id


def update_deal_stage(deal_id: str, stage: str) -> None:
    client = _get_client()
    client.crm.deals.basic_api.update(
        deal_id=deal_id,
        simple_public_object_input={"properties": {"dealstage": stage}},
    )


def update_deal_followup(deal_id: str, next_date: date, re_engagement_count: int | None = None) -> None:
    client = _get_client()
    props = {"closedate": next_date.isoformat()}
    if re_engagement_count is not None:
        props["hs_deal_stage_probability"] = str(re_engagement_count)
    client.crm.deals.basic_api.update(
        deal_id=deal_id,
        simple_public_object_input={"properties": props},
    )


# ── Activities / Notes ─────────────────────────────────────────────────────────

def log_note(contact_id: str, deal_id: str, body: str) -> None:
    """Log a note/activity on a contact and deal."""
    client = _get_client()
    engagement = {
        "engagement": {
            "active": True,
            "type": "NOTE",
            "timestamp": int(datetime.utcnow().timestamp() * 1000),
        },
        "metadata": {"body": body},
        "associations": {
            "contactIds": [int(contact_id)],
            "dealIds": [int(deal_id)],
        },
    }
    client.api_client.call_api(
        "/engagements/v1/engagements",
        "POST",
        body=engagement,
        response_type=object,
        auth_settings=["hapikey"],
        _return_http_data_only=True,
    )


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
