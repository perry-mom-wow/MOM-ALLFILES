"""SQLAlchemy 2.0 models for the EA's persistent state.

Schema follows Perry_EA_Master_Spec.md Section 10. All timestamps UTC.
Thread metadata retained 90 days then anonymised (GDPR).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.utcnow()


# ── File-backed state stored as JSON blobs ────────────────────────────────────
# Railway / Streamlit Cloud / any platform with an ephemeral filesystem wipes
# our queue/, data/sequences/, sent/ folders on every restart. To survive that,
# we mirror every JSON-file write into Postgres. On container start we hydrate
# the local files back from these tables (see state.file_sync).

class QueueFile(Base):
    """Mirror of queues/{rep_id}_{day}.json.
    Stored verbatim as a JSON list of queue items, keyed by (rep_id, day)."""
    __tablename__ = "queue_files"
    rep_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    items: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class SentFile(Base):
    """Mirror of sent/{rep_id}_{day}.json."""
    __tablename__ = "sent_files"
    rep_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    items: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class SequenceFile(Base):
    """Mirror of data/sequences/{deal_id}.json.
    The whole sequence payload (prospect info, messages dict, conversation
    block) is stored as JSON, keyed by deal_id."""
    __tablename__ = "sequence_files"
    deal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rep_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    prospect_name: Mapped[Optional[str]] = mapped_column(String(500))
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


# ── Gmail threads ──────────────────────────────────────────────────────────────

class Thread(Base):
    """Snapshot of a Gmail thread the EA has seen.

    `body_snippet` is intentionally short (<= 500 chars) so we never store full
    email bodies — only enough to render in the brief. Anonymised after 90 days.
    """
    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # Gmail thread ID
    subject: Mapped[Optional[str]] = mapped_column(String(500))
    sender_email: Mapped[Optional[str]] = mapped_column(String(320), index=True)
    sender_name: Mapped[Optional[str]] = mapped_column(String(200))
    classification: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    language: Mapped[Optional[str]] = mapped_column(String(8))  # 'en', 'pt'
    tier: Mapped[Optional[int]] = mapped_column(Integer, index=True)  # 1, 2, None
    body_snippet: Mapped[Optional[str]] = mapped_column(Text)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    last_action: Mapped[Optional[str]] = mapped_column(String(64))
    deadline: Mapped[Optional[date]] = mapped_column(Date)
    owner: Mapped[Optional[str]] = mapped_column(String(64))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    anonymised: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    drafts: Mapped[list["Draft"]] = relationship(back_populates="thread", cascade="all, delete-orphan")


# ── Drafts (queued for Perry's approval, or written to Gmail Drafts folder) ────

class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[Optional[str]] = mapped_column(ForeignKey("threads.id"), index=True)
    gmail_draft_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    recipient_email: Mapped[Optional[str]] = mapped_column(String(320))
    subject: Mapped[Optional[str]] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    voice_pass: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_violations: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    # status: pending | approved | rejected | sent | edited
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    decided_via: Mapped[Optional[str]] = mapped_column(String(32))  # 'brief_reply' | 'panel' | 'manual'

    thread: Mapped[Optional[Thread]] = relationship(back_populates="drafts")


# ── Derived to-do items surfaced into the brief ────────────────────────────────

class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    # source: gmail | calendar | granola | notion_brain_dump | hubspot
    source_ref: Mapped[Optional[str]] = mapped_column(String(200))  # thread_id, event_id, etc.
    title: Mapped[str] = mapped_column(String(500))
    detail: Mapped[Optional[str]] = mapped_column(Text)
    due: Mapped[Optional[date]] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    # status: open | done | dropped | held
    priority: Mapped[Optional[int]] = mapped_column(Integer)
    counterparty: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


# ── Commitments Perry made (extracted from threads / Granola / Brain Dump) ─────

class Commitment(Base):
    __tablename__ = "commitments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_ref: Mapped[Optional[str]] = mapped_column(String(200))
    counterparty: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    quote: Mapped[str] = mapped_column(Text)  # original phrasing
    target_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    # status: open | closed | stale | dropped
    closed_action: Mapped[Optional[str]] = mapped_column(Text)
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


# ── Run log ────────────────────────────────────────────────────────────────────

class DailyRun(Base):
    __tablename__ = "daily_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_date: Mapped[date] = mapped_column(Date, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    threads_processed: Mapped[int] = mapped_column(Integer, default=0)
    drafts_created: Mapped[int] = mapped_column(Integer, default=0)
    drafts_failed_voice: Mapped[int] = mapped_column(Integer, default=0)
    commitments_extracted: Mapped[int] = mapped_column(Integer, default=0)
    brief_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    errors: Mapped[Optional[dict]] = mapped_column(JSON)
    summary: Mapped[Optional[dict]] = mapped_column(JSON)


# ── Voice validator failure log (for tuning) ───────────────────────────────────

class VoiceValidationFailure(Base):
    __tablename__ = "voice_validation_failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    draft_excerpt: Mapped[str] = mapped_column(Text)
    violations: Mapped[dict] = mapped_column(JSON)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    final_pass: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)


# ── Reply-by-email log ─────────────────────────────────────────────────────────

class BriefReply(Base):
    __tablename__ = "brief_replies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    parsed_actions: Mapped[Optional[dict]] = mapped_column(JSON)
    actions_executed: Mapped[Optional[dict]] = mapped_column(JSON)
    error: Mapped[Optional[str]] = mapped_column(Text)
