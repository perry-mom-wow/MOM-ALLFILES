"""Server-side auto-find for missing prospect contact info.

Given a venue name and (optionally) its address, run a Tavily web search,
sweep the snippets, and extract:
  - the most plausible contact email (a real human one if visible, else
    the venue's generic info@/contact@/reservas@ as a sensible fallback)
  - LinkedIn URL
  - Instagram handle
  - phone number
  - the venue's website (so the caller can also derive `info@<domain>`)

This is the same data the researcher pulls during initial onboarding, but
exposed as a one-shot helper so the Daily Queue page can fill missing
fields on demand.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from tools.scraper import (
    scrape_url,
    extract_emails,
    extract_linkedin_url,
    extract_instagram_handle,
)
from tools.search import tavily_search

# Phone: tolerant — handles +351 prefix, spaces, parens, dots, dashes.
_PHONE_RE = re.compile(
    r"(\+?\d{1,3}[\s.\-]?)?(?:\(?\d{2,4}\)?[\s.\-]?)?\d{3}[\s.\-]?\d{3}[\s.\-]?\d{0,4}"
)

# Generic-but-useful inboxes (in priority order). If a personal email isn't
# visible, one of these from the venue's own domain is still actionable.
_GENERIC_LOCAL_PARTS = (
    "reservas", "reservations", "events", "eventos",
    "marketing", "geral", "hello", "contact", "contacto",
    "info", "office",
)


@dataclass
class FoundContacts:
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    instagram_handle: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    sources: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any((self.email, self.linkedin_url, self.instagram_handle, self.phone))

    def to_dict(self) -> dict:
        return {
            "email": self.email,
            "linkedin_url": self.linkedin_url,
            "instagram_handle": self.instagram_handle,
            "phone": self.phone,
            "website": self.website,
            "sources": self.sources,
        }


def _domain_of(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if not url.startswith("http"):
        url = "https://" + url
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host or None
    except Exception:
        return None


def _city_from_address(address: Optional[str]) -> str:
    if not address:
        return "Lisbon"
    # Use the last comma-segment as the city/area; fallback to Lisbon.
    last = address.split(",")[-1].strip()
    return last or "Lisbon"


def _pick_best_email(emails: list[str], venue_domain: Optional[str]) -> Optional[str]:
    """Prefer a personal-looking email on the venue's own domain. Otherwise
    prefer a generic on the venue's domain (reservas@, info@, ...). Otherwise
    take any plausible match."""
    if not emails:
        return None
    emails = [e.lower().strip() for e in emails if "@" in e]
    if not emails:
        return None

    on_domain = [e for e in emails if venue_domain and e.endswith("@" + venue_domain)]
    pool = on_domain or emails

    def is_personal(e: str) -> bool:
        local = e.split("@", 1)[0]
        return "." in local or "-" in local or len(local) > 8 and local.isalpha()

    personal = [e for e in pool if is_personal(e)]
    if personal:
        return personal[0]

    # Generic inboxes by priority order.
    for prefix in _GENERIC_LOCAL_PARTS:
        for e in pool:
            if e.startswith(prefix + "@"):
                return e

    return pool[0]


def _extract_phone(text: str) -> Optional[str]:
    """Pick the first plausible Portuguese-shaped phone number, normalised."""
    for m in _PHONE_RE.finditer(text):
        raw = m.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 9:
            continue
        # Re-format from the digits so we shed stray parens/spaces from the
        # source text (e.g. "(+351) 218 099 132" → "+351 218 099 132").
        if digits.startswith("351") and len(digits) >= 12:
            return f"+351 {digits[3:6]} {digits[6:9]} {digits[9:12]}"
        if len(digits) == 9:
            return f"{digits[0:3]} {digits[3:6]} {digits[6:9]}"
        return raw.lstrip(") ").strip()
    return None


def _is_real_instagram_handle(handle: str) -> bool:
    """Reject false positives from `extract_instagram_handle` that match the
    `@<domain>` half of an email address. Real IG handles never contain a
    public TLD like .com / .pt / .net."""
    if not handle:
        return False
    h = handle.lower()
    if any(tld in h for tld in (".com", ".pt", ".net", ".org", ".co", ".io", ".info")):
        return False
    return 2 <= len(h) <= 30


def _looks_like_venue_site(url: str, venue_name: str) -> bool:
    """Cheap heuristic: does the URL host contain a token from the venue name?"""
    host = _domain_of(url) or ""
    tokens = [t.lower() for t in re.split(r"[^A-Za-z0-9]+", venue_name) if len(t) >= 4]
    return any(t in host for t in tokens)


def auto_find_contacts(
    venue_name: str,
    address: Optional[str] = None,
    *,
    known_website: Optional[str] = None,
    max_tavily_results: int = 8,
) -> FoundContacts:
    """One-shot scrape: Tavily search → emails / LI / IG / phone / website."""
    out = FoundContacts()
    if not venue_name:
        return out

    city = _city_from_address(address)
    # Two complementary queries — contact page + general business listing.
    queries = [
        f'"{venue_name}" {city} contact email',
        f'"{venue_name}" {city} reservations email phone',
    ]

    snippets: list[str] = []
    candidate_sites: list[str] = []
    for q in queries:
        for r in tavily_search(q, max_results=max_tavily_results):
            url = r.get("url") or ""
            content = r.get("content") or ""
            snippets.append(content)
            if url and _looks_like_venue_site(url, venue_name):
                candidate_sites.append(url)
            out.sources.append(url)

    # Prefer a known website, else the most plausible candidate from results.
    site = known_website or (candidate_sites[0] if candidate_sites else None)
    if site:
        out.website = site
        # Best chance of catching contacts: scrape the venue's homepage too.
        try:
            page_text = scrape_url(site)
            if page_text:
                snippets.append(page_text)
        except Exception:
            pass

    haystack = "\n".join(snippets)
    venue_domain = _domain_of(out.website)

    out.email = _pick_best_email(extract_emails(haystack), venue_domain)
    out.linkedin_url = extract_linkedin_url(haystack)
    ig_raw = extract_instagram_handle(haystack)
    if ig_raw:
        ig_clean = ig_raw.lstrip("@").split("/")[0]
        if _is_real_instagram_handle(ig_clean):
            out.instagram_handle = ig_clean
    out.phone = _extract_phone(haystack)

    # Last-resort generic email: if we know the venue's domain but found no
    # email anywhere in the snippets/page, fall back to info@<domain>. Better
    # than nothing — Perry can edit before saving.
    if not out.email and venue_domain:
        out.email = f"info@{venue_domain}"

    return out
