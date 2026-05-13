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


# Tokens that appear in many venue names and don't distinguish anything.
# Stripped before computing distinctive venue tokens.
_GENERIC_VENUE_TOKENS: frozenset[str] = frozenset({
    "restaurante", "restaurant", "hotel", "cafe", "café", "bar", "club",
    "lisbon", "lisboa", "lisbonne", "porto", "comporta", "cascais",
    "the", "and", "and", "house", "group", "and", "lounge", "rooftop",
})


def _venue_tokens(venue_name: Optional[str]) -> list[str]:
    """Return tokens from the venue name that are likely distinctive."""
    if not venue_name:
        return []
    raw = re.split(r"[^A-Za-zÀ-ÿ]+", venue_name)
    return [
        t.lower() for t in raw
        if len(t) >= 4 and t.lower() not in _GENERIC_VENUE_TOKENS
    ]


def _slug_of(url: str) -> str:
    m = re.search(r"/in/([\w%\-\.]+)", url, flags=re.IGNORECASE)
    return m.group(1).lower() if m else ""


_PLACEHOLDER_LOCAL_RE = re.compile(
    r"^(?:"
    r"john[._-]?doe|jane[._-]?doe|firstname[._-]?lastname|firstname|lastname|"
    r"name|fullname|example|test|placeholder|user|email|you|me|"
    r"your[._-]?email|youremail|youraddress|sample"
    r")(?:[._-]|$|\d|@)",
    re.IGNORECASE,
)

_FREEMAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com",
    "hotmail.co.uk", "hotmail.fr", "hotmail.es", "live.com",
    "yahoo.com", "yahoo.co.uk", "yahoo.es", "ymail.com",
    "icloud.com", "me.com", "mac.com",
    "proton.me", "protonmail.com",
    "msn.com", "aol.com", "gmx.com", "gmx.de", "zoho.com",
})

_GENERIC_LOCAL_PARTS: frozenset[str] = frozenset({
    "info", "reservas", "reservations", "events", "eventos",
    "marketing", "geral", "general", "hello", "contact", "contacto",
    "contacts", "office", "admin", "front", "frontdesk", "frontoffice",
    "concierge", "stay", "booking", "bookings", "sales", "comercial",
})


def verify_email_address(
    email: str,
    venue_name: Optional[str] = None,
    contact_name: Optional[str] = None,
    website: Optional[str] = None,
) -> tuple[bool, str, str]:
    """Decide whether an email address is sendable.

    Returns (ok, severity, reason).
      severity: "hard" — clearly bogus, never send.
                "soft" — suspicious, surface for review but don't auto-clear.
                "ok"   — passes all checks.

    Hard fails (rejected on save, cleared by audit):
      - Placeholder local parts: john.doe@..., name@..., your.email@..., etc.
      - Local part is generic (info@, reservas@) on a domain unrelated to the venue.
      - Personal-shaped local on a domain that has no relation to the venue.

    Soft flags (kept, marked for review):
      - Personal email on a freemail provider (gmail/hotmail/outlook) whose
        local part doesn't contain a token from the contact_name.
      - Generic email (info@/reservas@) when contact_name is known and was
        likely a specific person we should reach instead.
    """
    if not email or "@" not in email:
        return False, "hard", "Not a valid email format."

    local, _, domain = email.lower().strip().partition("@")
    local = local.strip()
    domain = domain.strip()
    if not local or not domain or "." not in domain:
        return False, "hard", "Malformed email."

    # ── 1. Placeholder local-part ──
    if _PLACEHOLDER_LOCAL_RE.match(local):
        return False, "hard", (
            f"Local part '{local}' is a placeholder (john.doe, name, example, etc.) — "
            f"this email was never real, just a template guess."
        )

    is_generic = local in _GENERIC_LOCAL_PARTS
    is_freemail = domain in _FREEMAIL_DOMAINS

    # Helper: distinctive venue tokens
    venue_toks = _venue_tokens(venue_name) if venue_name else []
    venue_domain = _domain_of(website) if website else None
    venue_domain_root = venue_domain.split(":")[0] if venue_domain else None
    if venue_domain_root and venue_domain_root.startswith("www."):
        venue_domain_root = venue_domain_root[4:]

    # ── 2. Domain–venue correspondence ──
    domain_matches_website = bool(
        venue_domain_root and (
            domain == venue_domain_root or domain.endswith("." + venue_domain_root)
        )
    )
    domain_contains_venue_token = bool(
        venue_toks and any(t in domain for t in venue_toks)
    )
    if not is_freemail and not domain_matches_website and not domain_contains_venue_token:
        # Domain looks unrelated to the venue.
        # HARD only when venue has distinctive tokens but the domain matches
        # none of them — that's a clear scrape-from-wrong-page signal.
        # SOFT when venue tokens are too generic to verify (short name, no
        # distinctive words like "Sal Restaurant Comporta" → no 4-char tokens
        # survive after stop-list filtering).
        if venue_toks:
            return False, "hard", (
                f"Email domain '{domain}' has no obvious link to "
                f"'{venue_name}' (expected one of: {', '.join(venue_toks[:3])}). "
                f"Likely scraped from an unrelated page."
            )
        elif venue_name:
            return False, "soft", (
                f"Email domain '{domain}' doesn't obviously match '{venue_name}' "
                f"but the venue name has no distinctive tokens to verify against. "
                f"Eyeball this before sending."
            )

    # ── 3. Contact-name correspondence (only for personal-shaped locals) ──
    # Loosened: name mismatches on the venue's RIGHT domain are SOFT, not
    # HARD — the email still reaches the right org, it's just possibly a
    # different person (colleague, assistant, sales desk, etc.). Only HARD
    # when the name mismatch is on a *freemail* provider, where there's no
    # org affiliation we can trust.
    if not is_generic and contact_name:
        contact_tokens = _name_tokens(contact_name)
        if contact_tokens:
            last = contact_tokens[-1]
            first = contact_tokens[0]
            local_match = (last in local) or (first in local) or (
                len(first) >= 2 and first[0] in local and last in local
            )
            # Local also "matches" if it contains a distinctive venue token —
            # treat that as a venue-level inbox (e.g. "sheraton.lisboa@..." or
            # "altis.grand@altishotels.com"), not a wrong person.
            local_is_venue_inbox = bool(
                venue_toks and any(t in local for t in venue_toks)
            )
            if not local_match and not local_is_venue_inbox:
                if is_freemail:
                    return False, "soft", (
                        f"Personal '{email}' on a freemail provider doesn't "
                        f"match contact '{contact_name}'. Could be a colleague "
                        f"or wrong person — verify before sending."
                    )
                return False, "soft", (
                    f"Local '{local}' doesn't match contact '{contact_name}' "
                    f"but the domain '{domain}' is the venue's real org. "
                    f"Could be a colleague — verify before personalising."
                )

    # ── 4. Freemail accepted when contact-name matches local (or no contact known) ──
    if is_freemail and contact_name and not is_generic:
        # We've already covered the mismatch case above; here we accept.
        return True, "ok", f"Freemail address but local matches '{contact_name}'."

    if is_freemail:
        return True, "soft", (
            f"Freemail address ({domain}) — flag for caution. "
            f"Verify it actually belongs to this venue."
        )

    if is_generic and domain_matches_website:
        return True, "ok", "Generic inbox on the venue's own domain."

    if is_generic and domain_contains_venue_token:
        return True, "soft", (
            f"Generic inbox on '{domain}'. If you know a specific person, prefer them."
        )

    return True, "ok", "Passes all checks."


def verify_linkedin_url(
    url: str,
    contact_name: Optional[str],
    venue_name: Optional[str],
    *,
    city: Optional[str] = None,
) -> tuple[bool, str]:
    """Decide whether a LinkedIn URL we're about to save (or already saved)
    plausibly belongs to `contact_name` at `venue_name`.

    Two-stage check:

    1. SLUG STAGE (cheap, no API call):
       The URL slug must contain BOTH first AND last name tokens (or the
       single token if the contact name is one word). This eliminates
       obvious mismatches without burning a Tavily call.

    2. VENUE STAGE (API call, only when slug passes):
       Re-runs find_person_linkedin against the same (contact_name, venue)
       pair under the strict matcher. If the fresh search returns:
         - the same URL → verified (high confidence).
         - a different URL → reject (we trust strict-match more than the
           original save, which may have been made under the looser rule).
         - None → reject (no current evidence the URL belongs to this
           person at this venue).

    Returns (ok, reason).
    """
    if not url or "linkedin.com/in" not in url.lower():
        return False, "Not a LinkedIn /in/ URL."

    slug = _slug_of(url)
    if not slug:
        return False, "Could not parse profile slug from URL."

    tokens = _name_tokens(contact_name or "")
    if len(tokens) >= 2:
        first, last = tokens[0], tokens[-1]
        if not (first in slug and last in slug):
            return False, (
                f"Slug '{slug}' is missing first or last name from "
                f"'{contact_name}' (need both, found "
                f"first={first in slug} last={last in slug})."
            )
    elif tokens:
        if tokens[0] not in slug:
            return False, f"Slug '{slug}' is missing '{tokens[0]}'."
    else:
        # No contact name to verify against — fail closed; nudge caller to
        # supply one before saving a LinkedIn URL.
        return False, "No contact_name provided — cannot verify LinkedIn URL."

    # Stage 2: confirm the result actually associates with this venue.
    # Re-run the strict finder with the same inputs.
    try:
        fresh = find_person_linkedin(
            contact_name or "",
            venue_name=venue_name,
            city=city,
        )
    except Exception as e:
        # Network blip — be lenient: slug already matched, accept with caveat.
        return True, f"Slug matched; venue check skipped ({e})."

    if fresh and fresh.lower().rstrip("/") == url.lower().rstrip("/"):
        return True, "Verified by fresh search (URL matches)."
    if not fresh:
        return False, (
            f"Fresh search for '{contact_name}' at '{venue_name}' returned no "
            f"result — saved URL cannot be confirmed against this venue."
        )
    return False, (
        f"Fresh search returned a different URL ({fresh}). The saved URL "
        f"likely belonged to a different person of the same name."
    )


def _result_matches_person(
    url: str,
    title: str,
    snippet: str,
    name: str,
    *,
    venue_name: Optional[str] = None,
) -> bool:
    """Decide whether a Tavily result actually points at our named person
    AND, when a venue is known, that this person is associated with that
    specific venue (not a same-named person elsewhere).

    Acceptance criteria:
      A. NAME MATCH (always required):
         - URL slug contains both first AND last name tokens, OR
         - slug contains the last name AND the result title/snippet contains
           both first and last name.
      B. VENUE MATCH (required only when venue_name has distinctive tokens):
         - title OR snippet contains at least one distinctive venue token
           (≥4 chars, not in the generic stop-list).

    The venue-match check is what stops "Miguel Palma at Herdade da Comporta"
    from being returned for "Miguel Palma at Restaurante Via Graça". When the
    venue name is generic enough that no distinctive tokens survive the
    stop-list (e.g. just "Restaurante"), we fall back to name-match-only and
    accept the risk.
    """
    tokens = _name_tokens(name)
    if len(tokens) < 2:
        slug = _slug_of(url)
        if not (tokens and tokens[0] in slug):
            return False
    else:
        first, last = tokens[0], tokens[-1]
        slug = _slug_of(url)
        haystack_name = f"{title} {snippet}".lower()
        name_match = (
            (first in slug and last in slug)
            or (last in slug and first in haystack_name and last in haystack_name)
            or (first in haystack_name and last in haystack_name and "linkedin.com/in" in url.lower())
        )
        if not name_match:
            return False

    venue_toks = _venue_tokens(venue_name)
    if not venue_toks:
        # Venue name has no distinctive tokens (or none provided) — accept on name alone.
        return True

    haystack_full = f"{title} {snippet}".lower()
    return any(tok in haystack_full for tok in venue_toks)


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
            if _result_matches_person(
                candidate, title, snippet, contact_name, venue_name=venue_name,
            ):
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
