"""Re-enrichment: find contact details for HubSpot contacts that are missing them."""
from __future__ import annotations

import re
from typing import Optional

from tools import hubspot_client as hs
from tools.scraper import scrape_url, extract_emails, extract_linkedin_url, extract_instagram_handle
from tools.hunter import best_decision_maker
from tools.search import find_linkedin_decision_maker, tavily_search

# Pages we'll try to scrape for contact info (Portuguese + English variants)
CONTACT_PAGE_PATHS = [
    "/contact", "/contacto", "/contactos", "/contact-us",
    "/sobre", "/sobre-nos", "/about", "/about-us",
    "/equipa", "/team", "/info",
    "",  # homepage as fallback
]


def _domain_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"(?:https?://)?(?:www\.)?([^/]+)", url)
    return m.group(1) if m else None


def _extract_phone(text: str) -> Optional[str]:
    """Extract a Portuguese phone number from page text."""
    # +351 + 9 digits, or 9 digits starting with 2/3/9 (PT mobile/landline)
    patterns = [
        r"\+?351[\s\-\.]?(\d{3}[\s\-\.]?\d{3}[\s\-\.]?\d{3})",
        r"\b((?:9[1236]|2\d|3\d)\d[\s\-\.]?\d{3}[\s\-\.]?\d{3})\b",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return "+351 " + m.group(1).replace(" ", "").replace("-", "").replace(".", "")
    return None


def _deep_scrape_for_contact(website: str) -> dict:
    """Try multiple contact-page paths to find email, phone, LinkedIn, Instagram."""
    base = website.rstrip("/")
    found = {"email": None, "phone": None, "linkedin": None, "instagram": None}

    for path in CONTACT_PAGE_PATHS:
        url = base + path
        text = scrape_url(url)
        if not text or text.startswith("[scrape failed"):
            continue

        if not found["email"]:
            emails = extract_emails(text)
            if emails:
                found["email"] = emails[0]

        if not found["phone"]:
            phone = _extract_phone(text)
            if phone:
                found["phone"] = phone

        if not found["linkedin"]:
            li = extract_linkedin_url(text)
            if li:
                found["linkedin"] = li

        if not found["instagram"]:
            ig = extract_instagram_handle(text)
            if ig:
                found["instagram"] = ig

        # Stop early if we got everything
        if all(found.values()):
            break

    return found


def _get_contacts_without_emails() -> list[dict]:
    """Pull all HubSpot contacts and return those missing an email."""
    client = hs._get_client()
    props = ["firstname", "lastname", "email", "phone", "company", "linkedin_bio",
             "twitterhandle", "associatedcompanyid"]
    results = []
    after = None
    while True:
        kwargs: dict = {"limit": 100, "properties": props}
        if after:
            kwargs["after"] = after
        page = client.crm.contacts.basic_api.get_page(**kwargs)
        for c in page.results:
            if not (c.properties or {}).get("email"):
                results.append({"id": c.id, "properties": c.properties or {}})
        if not page.paging or not page.paging.next:
            break
        after = page.paging.next.after
    return results


def _company_by_id(company_id: str) -> Optional[dict]:
    try:
        company = hs._get_client().crm.companies.basic_api.get_by_id(
            company_id=company_id,
            properties=["name", "domain", "website", "phone"],
        )
        return {"id": company_id, "properties": company.properties or {}}
    except Exception:
        return None


def _get_company_for_contact(contact_id: str) -> Optional[dict]:
    """Find the company associated with a contact. Tries 3 methods in order."""
    client = hs._get_client()

    # Method 1: contact's associatedcompanyid property (set automatically when associated)
    try:
        contact = client.crm.contacts.basic_api.get_by_id(
            contact_id=contact_id, properties=["associatedcompanyid"],
        )
        cid = (contact.properties or {}).get("associatedcompanyid")
        if cid:
            return _company_by_id(cid)
    except Exception:
        pass

    # Method 2: v4 contact → company association
    try:
        assoc = client.crm.associations.v4.basic_api.get_page(
            object_type="contact", object_id=contact_id, to_object_type="company",
        )
        if assoc.results:
            return _company_by_id(assoc.results[0].to_object_id)
    except Exception:
        pass

    # Method 3: contact → deal → company (every contact has a deal in our flow)
    try:
        deal_assoc = client.crm.associations.v4.basic_api.get_page(
            object_type="contact", object_id=contact_id, to_object_type="deal",
        )
        if deal_assoc.results:
            deal_id = deal_assoc.results[0].to_object_id
            company_assoc = client.crm.associations.v4.basic_api.get_page(
                object_type="deal", object_id=deal_id, to_object_type="company",
            )
            if company_assoc.results:
                return _company_by_id(company_assoc.results[0].to_object_id)
    except Exception:
        pass

    return None


def re_enrich_all() -> dict:
    """Find contacts without emails and try to enrich them with /contact-page scraping + Hunter + LinkedIn."""
    print("📥 Fetching contacts without emails from HubSpot...")
    contacts = _get_contacts_without_emails()
    print(f"   Found {len(contacts)} contacts missing email.\n")

    summary = {"checked": len(contacts), "enriched": 0, "still_missing": 0, "details": []}
    if not contacts:
        return summary

    for c in contacts:
        cid = c["id"]
        props = c["properties"]
        first = props.get("firstname") or "?"
        last = props.get("lastname") or ""
        company = _get_company_for_contact(cid)
        if not company:
            print(f"   ⏭  {first} {last}: no associated company — skipping")
            continue

        cprops = company["properties"]
        venue_name = cprops.get("name") or ""
        website = cprops.get("website") or ""
        domain = cprops.get("domain") or _domain_from_url(website)
        print(f"   🔍 {venue_name} (contact: {first} {last})")

        found = {"email": None, "phone": None, "linkedin": None, "instagram": None,
                 "decision_maker_name": None, "decision_maker_title": None}

        # 1. Deep scrape contact pages
        if website:
            scraped = _deep_scrape_for_contact(website)
            for k, v in scraped.items():
                if v and not found.get(k):
                    found[k] = v

        # 2. Try Hunter again on the domain (sometimes data updates)
        if domain and not found.get("email"):
            dm = best_decision_maker(domain)
            if dm:
                found["email"] = dm.get("email") or found["email"]
                found["linkedin"] = dm.get("linkedin") or found["linkedin"]
                found["decision_maker_name"] = f"{dm.get('first_name','')} {dm.get('last_name','')}".strip()
                found["decision_maker_title"] = dm.get("position")

        # 3. LinkedIn fallback (now softer)
        if not found.get("decision_maker_name"):
            li = find_linkedin_decision_maker(venue_name, "Lisboa, Portugal")
            if li:
                found["decision_maker_name"] = li.get("name")
                found["decision_maker_title"] = li.get("title")
                found["linkedin"] = li.get("linkedin_url") or found["linkedin"]

        # Update HubSpot contact if we found anything new
        updates = {}
        if found.get("email"): updates["email"] = found["email"]
        if found.get("phone") and not props.get("phone"): updates["phone"] = found["phone"]
        if found.get("linkedin") and not props.get("linkedin_bio"): updates["linkedin_bio"] = found["linkedin"]
        if found.get("instagram") and not props.get("twitterhandle"): updates["twitterhandle"] = found["instagram"]
        if found.get("decision_maker_name"):
            parts = found["decision_maker_name"].split(" ", 1)
            if parts[0] and (props.get("firstname") in (None, "Unknown", "?")):
                updates["firstname"] = parts[0]
            if len(parts) > 1 and not props.get("lastname"):
                updates["lastname"] = parts[1]

        if updates:
            try:
                hs._get_client().crm.contacts.basic_api.update(
                    contact_id=cid,
                    simple_public_object_input={"properties": updates},
                )
                summary["enriched"] += 1
                kinds = ", ".join(updates.keys())
                print(f"      ✅ Updated: {kinds}")
                summary["details"].append({"venue": venue_name, "updated": list(updates.keys())})
            except Exception as e:
                print(f"      ⚠️  HubSpot update failed: {e}")
        else:
            summary["still_missing"] += 1
            print(f"      ⏭  Still no contact info found")

    print(f"\n✅ Enrichment complete. Enriched: {summary['enriched']} / "
          f"Still missing: {summary['still_missing']} / Total checked: {summary['checked']}\n")
    return summary
