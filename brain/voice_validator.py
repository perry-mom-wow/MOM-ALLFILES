"""Voice validator. Pure-Python regex rules from Perry_EA_Master_Spec.md §10.

A draft passes only if zero hard violations. Soft warnings (e.g., banned phrase
in quoted text) are surfaced but don't fail the draft. Failures are logged to
state.VoiceValidationFailure for tuning.

Public API:
    validate(draft: str, *, archetype: str = "default") -> ValidationResult
    is_valid(draft: str, *, archetype: str = "default") -> bool

archetype:
    "default"  — short reply, follow-up, decline, vendor financial
    "cold"     — cold outreach (extra rules: word count, permission line, single ask)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


BANNED_PHRASES: tuple[str, ...] = (
    "stands as",
    "serves as",
    "vibrant",
    "rich",
    "captivating",
    "fascinating",
    "transformative",
    "remarkable",
    "in summary",
    "overall",
    "in conclusion",
    "ultimately",
    "embodies",
    "plays a significant role",
    # AI-tells from agents/writer.py BANNED set, kept consistent
    "i hope this finds you well",
    "i hope you're well",
    "i hope you are well",
    "in today's fast-paced world",
    "in today's landscape",
    "circle back",
    "circling back",
    "touching base",
    "leverage",
    "synergy",
    "unlock",
    "ecosystem",
    "exciting opportunity",
    "quick question",
    "i wanted to reach out",
    "my name is",
    # Padding openers
    "it's important to note",
    "it's worth mentioning",
    "one of the most important things",
)

# Spam-trigger words (3+ in body = 67% likelier to land in spam per Folderly).
# Banned in COLD archetype only — followups can use "free" in context like
# "free 2-pack drop-off" etc, but cold openers must avoid these entirely.
COLD_SPAM_TRIGGERS: tuple[str, ...] = (
    "free",
    "guaranteed",
    "act now",
    "limited time",
    "risk-free",
    "risk free",
    "urgent",
    "100%",
    "cash",
    "winner",
    "earn extra",
    "no obligation",
)

# Calendar-link domains banned on first touch — Gong data: calendar links on
# cold reduce primary-inbox placement and depress reply rate. Save for touch
# #2-3 once they've engaged.
COLD_CALENDAR_LINK_PATTERNS: tuple[str, ...] = (
    "calendly.com",
    "cal.com",
    "meetings.hubspot.com",
    "savvycal.com",
    "scheduleonce",
    "x.ai/",  # x.ai calendar bot
    "outlook.com/owa",
    "calendar.google.com",
    "calendar.app.google",
)

NEGATIVE_PARALLELISM_PATTERNS: tuple[str, ...] = (
    r"\bnot only\s+\w[\w\s,]{0,40}?\bbut also\b",
    r"\bit'?s not just\s+\w[\w\s,]{0,40}?\bit'?s\b",
    r"\bmore than just a\b",
)

# Cold-outreach-only banned openers
COLD_BANNED_OPENERS: tuple[str, ...] = (
    "i hope this finds you well",
    "quick question",
    "touching base",
    "circling back",
    "circle back",
)

PERMISSION_PHRASES: tuple[str, ...] = (
    "no pressure",
    "if not",
    "no worries",
    "i'll go quiet",
    "happy to hear no",
    "won't push",
)


@dataclass
class Violation:
    rule: str
    detail: str
    severity: Literal["hard", "soft"] = "hard"

    def to_dict(self) -> dict:
        return {"rule": self.rule, "detail": self.detail, "severity": self.severity}


@dataclass
class ValidationResult:
    passed: bool
    violations: list[Violation] = field(default_factory=list)
    archetype: str = "default"

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "archetype": self.archetype,
            "violations": [v.to_dict() for v in self.violations],
        }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _strip_signature(text: str) -> str:
    """Remove sign-off block from the body so signature elements don't trigger
    rules meant for the body (e.g., emoji in signature is allowed)."""
    for marker in ("\nKindly,", "\nKindly\n", "\nMush Love", "\n--", "\nBest,"):
        idx = text.find(marker)
        if idx >= 0:
            return text[:idx]
    return text


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _bold_count(text: str) -> int:
    """Count markdown bold occurrences. Each pair of ** counts as one bold."""
    return len(re.findall(r"\*\*[^*\n]+\*\*", text))


# ── Individual rule checks ─────────────────────────────────────────────────────

def _check_double_hyphens(body: str) -> list[Violation]:
    if "--" in body:
        return [Violation("no_double_hyphens", "Found '--' in body.")]
    return []


def _check_em_dashes(body: str) -> list[Violation]:
    em_count = body.count("\u2014") + body.count("\u2013")  # — and –
    if em_count > 1:
        return [Violation(
            "max_one_em_dash",
            f"Found {em_count} em/en dashes; max 1 per draft.",
        )]
    return []


def _check_banned_phrases(body: str) -> list[Violation]:
    lower = body.lower()
    out: list[Violation] = []
    for phrase in BANNED_PHRASES:
        if phrase in lower:
            out.append(Violation(
                "banned_phrase",
                f"Contains banned phrase: '{phrase}'.",
            ))
    return out


def _check_negative_parallelism(body: str) -> list[Violation]:
    out: list[Violation] = []
    for pat in NEGATIVE_PARALLELISM_PATTERNS:
        match = re.search(pat, body, flags=re.IGNORECASE)
        if match:
            out.append(Violation(
                "no_negative_parallelism",
                f"Pattern '{match.group(0)[:60]}'.",
            ))
    return out


def _check_bold_count(body: str) -> list[Violation]:
    n = _bold_count(body)
    if n > 1:
        return [Violation(
            "max_one_bold",
            f"Found {n} markdown-bold spans; max 1 per draft.",
        )]
    return []


def _check_padding_openers(body: str) -> list[Violation]:
    """First 80 chars shouldn't start with a known padding opener."""
    head = body.lstrip()[:120].lower()
    out: list[Violation] = []
    for opener in (
        "it's important to note",
        "in today's",
        "it's worth mentioning",
        "one of the most important things",
    ):
        if head.startswith(opener):
            out.append(Violation(
                "no_padding_opener",
                f"Opens with '{opener}'.",
            ))
    return out


# ── Cold-outreach extra rules ──────────────────────────────────────────────────
# Canonical spec: Notion Wiki "📨 Cold Email Construction Rules" (2026-05-12).
# Empirical basis: Boomerang 40M / Backlinko 12M / Gong 85M / Belkins 16.5M.

def _check_cold_word_count(body: str) -> list[Violation]:
    """Cold body: 50-100 words. Target 50-75. Hard fail outside [50, 100]."""
    wc = _word_count(body)
    if wc < 50 or wc > 100:
        return [Violation(
            "cold_word_count",
            f"Cold body word count {wc}; must be 50-100 (target 50-75). "
            f"Past 100 words reply rate drops sharply.",
        )]
    return []


def _check_cold_permission(body: str) -> list[Violation]:
    """SOFT — permission language is encouraged but not required for touch #1."""
    lower = body.lower()
    if not any(p in lower for p in PERMISSION_PHRASES):
        return [Violation(
            "cold_permission_encouraged",
            "No permission language found ('no pressure', 'if not', etc.). "
            "Recommended for the P.S. line but not required.",
            severity="soft",
        )]
    return []


def _check_cold_banned_openers(body: str) -> list[Violation]:
    head = body.lstrip()[:200].lower()
    out: list[Violation] = []
    for opener in COLD_BANNED_OPENERS:
        if opener in head:
            out.append(Violation(
                "cold_banned_opener",
                f"Cold opener uses banned phrase '{opener}'.",
            ))
    return out


def _check_cold_subject(subject: str | None) -> list[Violation]:
    if subject is None:
        return []
    out: list[Violation] = []
    wc = _word_count(subject)
    if wc < 1 or wc > 5:
        out.append(Violation(
            "cold_subject_length",
            f"Subject '{subject}' has {wc} words; must be 1-5.",
        ))
    # Lowercase rule — Gong: lowercase outperforms title-case.
    # Allow proper nouns: subject must equal its lowercase form for at least
    # 80% of its alphabetic characters.
    alpha = [c for c in subject if c.isalpha()]
    if alpha:
        lower_ratio = sum(1 for c in alpha if c.islower()) / len(alpha)
        if lower_ratio < 0.85:
            out.append(Violation(
                "cold_subject_case",
                f"Subject '{subject}' has too much title/upper case; "
                f"cold subjects must be ≥85% lowercase. Found {lower_ratio:.0%}.",
            ))
    return out


def _check_cold_spam_triggers(body: str) -> list[Violation]:
    """Folderly: 3+ promo trigger words → 67% likelier to land in spam."""
    lower = body.lower()
    out: list[Violation] = []
    for trigger in COLD_SPAM_TRIGGERS:
        # Word-boundary check so 'free' doesn't match 'freedom', 'cash' not 'cashews'.
        if re.search(rf"\b{re.escape(trigger)}\b", lower):
            out.append(Violation(
                "cold_spam_trigger",
                f"Contains spam-trigger word '{trigger}'.",
            ))
    return out


def _check_cold_no_calendar_link(body: str) -> list[Violation]:
    """Calendar links depress reply rate on cold (Gong). Save for touch #2-3."""
    lower = body.lower()
    out: list[Violation] = []
    for pattern in COLD_CALENDAR_LINK_PATTERNS:
        if pattern in lower:
            out.append(Violation(
                "cold_calendar_link",
                f"Calendar link ('{pattern}') is banned on cold touch #1. "
                f"Use an interest-CTA question instead.",
            ))
    return out


def _check_cold_max_one_link(body: str) -> list[Violation]:
    """Cold touch #1: max one http(s):// link. Multiple links → spam classifier."""
    links = re.findall(r"https?://\S+", body)
    if len(links) > 1:
        return [Violation(
            "cold_max_one_link",
            f"Found {len(links)} links; cold touch #1 allows at most 1.",
        )]
    return []


# ── Public API ─────────────────────────────────────────────────────────────────

def validate(
    draft: str,
    *,
    archetype: str = "default",
    subject: str | None = None,
) -> ValidationResult:
    """Run all rules. Returns ValidationResult; passed = no hard violations."""
    body = _strip_signature(draft)
    violations: list[Violation] = []

    violations.extend(_check_double_hyphens(body))
    violations.extend(_check_em_dashes(body))
    violations.extend(_check_banned_phrases(body))
    violations.extend(_check_negative_parallelism(body))
    violations.extend(_check_bold_count(body))
    violations.extend(_check_padding_openers(body))

    if archetype == "cold":
        violations.extend(_check_cold_word_count(body))
        violations.extend(_check_cold_banned_openers(body))
        violations.extend(_check_cold_subject(subject))
        violations.extend(_check_cold_spam_triggers(body))
        violations.extend(_check_cold_no_calendar_link(body))
        violations.extend(_check_cold_max_one_link(body))
        violations.extend(_check_cold_permission(body))  # soft check, surfaces as warning only

    hard = [v for v in violations if v.severity == "hard"]
    return ValidationResult(passed=not hard, violations=violations, archetype=archetype)


def is_valid(draft: str, *, archetype: str = "default", subject: str | None = None) -> bool:
    return validate(draft, archetype=archetype, subject=subject).passed
