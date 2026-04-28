"""Migrate 37 deals (+ their companies & contacts) from the old portal to Marcus's portal.

Reads from old portal (HUBSPOT_API_KEY) and writes to new portal (HUBSPOT_API_KEY_MARCUS).
Saves migration/deal_id_map.json mapping old → new deal IDs so we can fix up queue files next.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Clear inherited Anthropic vars + load .env
for _v in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
    os.environ.pop(_v, None)
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=True)

import hubspot  # noqa: E402
from hubspot.crm.contacts import SimplePublicObjectInputForCreate as ContactInput  # noqa: E402
from hubspot.crm.companies import SimplePublicObjectInputForCreate as CompanyInput  # noqa: E402
from hubspot.crm.deals import SimplePublicObjectInputForCreate as DealInput  # noqa: E402
from hubspot.crm.associations.v4 import AssociationSpec  # noqa: E402

OLD_TOKEN = os.environ["HUBSPOT_API_KEY"]
NEW_TOKEN = os.environ["HUBSPOT_API_KEY_MARCUS"]

PIPELINE_TIER_1 = "default"
PIPELINE_TIER_23 = "3713146104"

# Old portal HubSpot stage IDs → our internal stage name
OLD_STAGE_TO_OURS = {
    "appointmentscheduled": "prospect",
    "qualifiedtobuy": "contacted",
    "presentationscheduled": "replied",
    "decisionmakerboughtin": "tasting_booked",
    "contractsent": "tasting_booked",
    "closedwon": "won",
    "closedlost": "lost",
}

# New portal: pipeline → (our_stage_name → HubSpot stage ID)
STAGE_MAP_BY_PIPELINE = {
    PIPELINE_TIER_1: {
        "prospect": "appointmentscheduled",
        "contacted": "qualifiedtobuy",
        "replied": "presentationscheduled",
        "tasting_booked": "contractsent",
        "won": "closedwon",
        "lost": "closedlost",
    },
    PIPELINE_TIER_23: {
        "prospect": "5143548138",
        "contacted": "5143548139",
        "replied": "5143548140",
        "tasting_booked": "5143548142",
        "won": "5143548143",
        "lost": "5143548144",
    },
}


def tier_from_amount(amount: float) -> int:
    if amount >= 900:
        return 1
    if amount >= 500:
        return 2
    return 3


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


def _create_tolerant(create_fn, props: dict) -> object:
    """Create object, retrying without missing properties if HubSpot rejects them."""
    while True:
        try:
            return create_fn(props)
        except Exception as e:
            haystack = str(getattr(e, "body", "") or "") + " " + str(e)
            missing = re.findall(r'Property\s+\\?"([^"\\]+)\\?"\s+does not exist', haystack)
            if not missing or not any(m in props for m in missing):
                raise
            for m in missing:
                props.pop(m, None)
            print(f"      ⚠️  dropped missing properties: {missing}")


def upsert_company_new(new_client, name: str, domain: str | None, website: str | None) -> str:
    # Try search by name
    try:
        results = new_client.crm.companies.search_api.do_search(
            public_object_search_request={
                "filterGroups": [{"filters": [{"propertyName": "name", "operator": "EQ", "value": name}]}],
                "limit": 1,
            }
        )
        if results.results:
            return results.results[0].id
    except Exception:
        pass
    props = {"name": name}
    if domain:
        props["domain"] = domain
    if website:
        props["website"] = website
    resp = _create_tolerant(
        lambda p: new_client.crm.companies.basic_api.create(
            simple_public_object_input_for_create=CompanyInput(properties=p)
        ),
        props,
    )
    return resp.id


def upsert_contact_new(new_client, props: dict, company_id: str) -> str | None:
    email = props.get("email")
    if email:
        try:
            results = new_client.crm.contacts.search_api.do_search(
                public_object_search_request={
                    "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
                    "limit": 1,
                }
            )
            if results.results:
                cid = results.results[0].id
                _associate(new_client, "contact", cid, "company", company_id, 280)
                return cid
        except Exception:
            pass
    if not props.get("firstname") and not props.get("lastname") and not email:
        return None
    resp = _create_tolerant(
        lambda p: new_client.crm.contacts.basic_api.create(
            simple_public_object_input_for_create=ContactInput(properties=p)
        ),
        props,
    )
    cid = resp.id
    _associate(new_client, "contact", cid, "company", company_id, 280)
    return cid


def main() -> None:
    old = hubspot.Client.create(access_token=OLD_TOKEN)
    new = hubspot.Client.create(access_token=NEW_TOKEN)

    # 1. Pull every deal from old portal
    deals = []
    after = None
    while True:
        kwargs = {"limit": 100, "properties": [
            "dealname", "dealstage", "amount", "closedate", "createdate", "pipeline",
        ]}
        if after:
            kwargs["after"] = after
        page = old.crm.deals.basic_api.get_page(**kwargs)
        deals.extend(page.results)
        if not page.paging or not page.paging.next:
            break
        after = page.paging.next.after
    print(f"Pulled {len(deals)} deals from old portal.\n")

    id_map: dict[str, str] = {}
    summary = {"migrated": 0, "errors": 0, "skipped": 0}

    for i, deal in enumerate(deals, 1):
        old_deal_id = deal.id
        props = deal.properties or {}
        name = props.get("dealname", "(unnamed)")
        amount = float(props.get("amount") or 0)
        tier = tier_from_amount(amount)
        old_stage = (props.get("dealstage") or "").lower()
        our_stage = OLD_STAGE_TO_OURS.get(old_stage, "prospect")
        closedate = (props.get("closedate") or "")[:10] or None

        print(f"[{i}/{len(deals)}] {name}  (Tier {tier}, {our_stage})")

        try:
            # Pull associated company
            company_id_new = None
            try:
                comp_assoc = old.crm.associations.v4.basic_api.get_page(
                    object_type="deal", object_id=old_deal_id, to_object_type="company",
                )
                if comp_assoc.results:
                    old_comp_id = comp_assoc.results[0].to_object_id
                    comp = old.crm.companies.basic_api.get_by_id(
                        company_id=old_comp_id,
                        properties=["name", "domain", "website", "phone"],
                    )
                    cprops = comp.properties or {}
                    company_id_new = upsert_company_new(
                        new, cprops.get("name") or name.split(" · ")[0],
                        cprops.get("domain"), cprops.get("website"),
                    )
            except Exception as e:
                print(f"    ⚠️  company assoc skipped: {e}")
            if not company_id_new:
                # Fall back to create company by deal name
                company_id_new = upsert_company_new(new, name.split(" · ")[0], None, None)

            # Pull associated contact
            contact_id_new = None
            try:
                cont_assoc = old.crm.associations.v4.basic_api.get_page(
                    object_type="deal", object_id=old_deal_id, to_object_type="contact",
                )
                if cont_assoc.results:
                    old_cont_id = cont_assoc.results[0].to_object_id
                    cont = old.crm.contacts.basic_api.get_by_id(
                        contact_id=old_cont_id,
                        properties=["firstname", "lastname", "email", "phone", "jobtitle"],
                    )
                    cprops = {k: v for k, v in (cont.properties or {}).items()
                              if v and k in ("firstname", "lastname", "email", "phone", "jobtitle")}
                    if cprops:
                        contact_id_new = upsert_contact_new(new, cprops, company_id_new)
            except Exception as e:
                print(f"    ⚠️  contact assoc skipped: {e}")

            # Create deal in correct pipeline + stage
            pipeline = PIPELINE_TIER_1 if tier == 1 else PIPELINE_TIER_23
            stage_id = STAGE_MAP_BY_PIPELINE[pipeline].get(our_stage,
                          STAGE_MAP_BY_PIPELINE[pipeline]["prospect"])
            deal_props = {
                "dealname": name,
                "pipeline": pipeline,
                "dealstage": stage_id,
                "amount": str(amount),
            }
            if closedate:
                deal_props["closedate"] = closedate
            new_deal_resp = _create_tolerant(
                lambda p: new.crm.deals.basic_api.create(
                    simple_public_object_input_for_create=DealInput(properties=p)
                ),
                deal_props,
            )
            new_deal_id = new_deal_resp.id
            _associate(new, "deal", new_deal_id, "company", company_id_new, 341)
            if contact_id_new:
                _associate(new, "deal", new_deal_id, "contact", contact_id_new, 3)

            id_map[old_deal_id] = new_deal_id
            summary["migrated"] += 1
            print(f"    ✅ {old_deal_id} → {new_deal_id} (pipeline {pipeline})")
        except Exception as e:
            summary["errors"] += 1
            print(f"    ❌ {old_deal_id}: {e}")

    # Save mapping for next step (queue file fixup)
    out = ROOT / "migration" / "deal_id_map.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(id_map, indent=2))
    print(f"\nSaved id map → {out}")
    print(f"Migrated: {summary['migrated']}  Errors: {summary['errors']}")


if __name__ == "__main__":
    main()
