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


_LINKEDIN_PROFILE_RE = re.compile(
    r"https?://(?:[a-z]{2}\.)?(?:www\.)?linkedin\.com/in/[\w%\-\.]+",
    re.IGNORECASE,
)


def _name_tokens(name: str) -> list[str]:
    """First/last name tokens, ≥3 chars, lowercased — used to filter out
    LinkedIn URLs that hit a different person."""
    return [t.lower() for t in re.split(r"[^A-Za-zÀ-ÿ]+", name or "") if len(t) >= 3]


def _slug_of(url: str) -> str:
    m = re.search(r"/in/([\w%\-\.]+)", url, flags=re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _result_matches_person(url: str, title: str, snippet: str, name: str) -> bool:
    """Decide whether a Tavily result actually points at our named person.

    Tightened from the earlier "any token matches slug" to avoid hitting
    different people who share a first name. Two acceptance paths:

    1. The URL slug contains BOTH first and last name tokens (e.g. miguel-palma).
    2. The slug contains at least the LAST name token AND the title or
       snippet contains the full first+last name combo.
    """
    tokens = _name_tokens(name)
    if len(tokens) < 2:
        # Single-token name — fall back to slug-contains-token (rare).
        slug = _slug_of(url)
        return bool(tokens) and tokens[0] in slug

    first, last = tokens[0], tokens[-1]
    slug = _slug_of(url)
    haystack = f"{title} {snippet}".lower()

    if first in slug and last in slug:
        return True
    if last in slug and first in haystack and last in haystack:
        return True
    # Last-resort: profile URL slug doesn't carry the name (e.g. it's a hash),
    # but the title clearly says it's them.
    if first in haystack and last in haystack and "linkedin.com/in" in url.lower():
        return True
    return False


def find_person_linkedin(
    contact_name: str,
    venue_name: Optional[str] = None,
    city: str = "Lisbon",
    *,
    max_results: int = 5,
) -> Optional[str]:
    """Direct Tavily search for `<name>`'s LinkedIn profile.

    Returns the first linkedin.com/in/<slug> URL whose slug contains a token
    from the person's name. Tavily's `url` field is the source-of-truth here
    (more reliable than snippet scraping). Returns None if nothing matches.
    """
    if not contact_name or not contact_name.strip():
        return None

    queries: list[str] = []
    if venue_name:
        queries.append(f'site:linkedin.com/in "{contact_name}" "{venue_name}"')
        queries.append(f'"{contact_name}" "{venue_name}" linkedin')
    queries.append(f'site:linkedin.com/in "{contact_name}" {city}')
    queries.append(f'"{contact_name}" {city} linkedin')

    seen: set[str] = set()
    for q in queries:
        for r in tavily_search(q, max_results=max_results):
            url = (r.get("url") or "").strip()
            title = (r.get("title") or "").strip()
            snippet = (r.get("content") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            # Sometimes Tavily returns linkedin.com/posts/... or /pulse/...
            # Only accept canonical /in/ profile URLs.
            m = _LINKEDIN_PROFILE_RE.search(url)
            if not m:
                m = _LINKEDIN_PROFILE_RE.search(snippet)
            if not m:
                continue
            candidate = m.group(0)
            if _result_matches_person(candidate, title, snippet, contact_name):
                return candidate
    return None


def auto_find_contacts(
    venue_name: str,
    address: Optional[str] = None,
    *,
    known_website: Optional[str] = None,
    contact_name: Optional[str] = None,
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

    # If LinkedIn wasn't found in the venue's snippets but we know who the
    # contact is, run a direct search for that person's profile.
    if not out.linkedin_url and contact_name:
        try:
            city = _city_from_address(address)
            person_li = find_person_linkedin(contact_name, venue_name=venue_name, city=city)
            if person_li:
                out.linkedin_url = person_li
                out.sources.append(person_li)
        except Exception:
            pass
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
