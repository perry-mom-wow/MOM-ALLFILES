# MOM Sales OS — Project Brief

**Owner:** Perry Patraszewski, Founder, MOM | Longevity Alchemists
**For:** Claude Code build agent
**Repo:** extends the existing `juice-sales-agent` (HubSpot + Resend + Streamlit)

---

## 1. Goal

One Claude Code application that runs MOM's B2B commercial workflow end to end. Bolt onto the existing repo, extend rather than replace. The founder stays in the loop only at decision points and brand-voice approvals; everything else runs autonomously and reports back daily.

## 2. Functional scope

The system must handle the full revenue cycle:

1. **Outreach** — cold emails to ICP-fit prospects from Perry's address.
2. **Follow-ups** — sequenced cadence (Day 3, 7, 14, then nurture) until reply.
3. **Tasting bookings** — capture and confirm calendar slots when a prospect is ready.
4. **Order capture** — parse incoming order emails + a manual entry form in the panel.
5. **Production handoff** — push every confirmed order to the MOM production sheet within 2 hours.
6. **Invoicing** — auto-generate the matching invoice in Moloni and send it to the client.
7. **AR follow-ups** — chase unpaid invoices on a 7 / 14 / 21 / 30-day cadence.
8. **Event requests** — intake, production capacity check, and brief generation for the production team.
9. **Loose-ends sweep** — daily inbox triage that surfaces commitments, stale threads, and draft replies.
10. **Daily oversight** — 06:00 Lisbon brief via Resend + Streamlit panel for approve/reject/edit.

## 3. Architecture

Reuse what's already in the repo (`agents/`, `config/`, `tools/`, `dashboard/`). Add new modules:

```
connectors/   gmail, calendar, moloni              (thin typed wrappers)
brain/        triage, drafter, synthesizer         (LLM classification + drafting)
orders/       capture, production_handoff
invoices/     moloni_issue, ar_chaser
events/       intake, capacity_check, brief_gen
reports/      daily_brief, weekly_digest
panel/        streamlit pages: today / drafts / threads / orders / invoices / events
orchestrator/ run_daily.py                         (cron entry point)
state/        SQLAlchemy models + Alembic          (Postgres on Supabase)
```

Existing modules to keep and extend: `agents/discovery.py`, `agents/researcher.py`, `agents/writer.py`, `agents/sequencer.py`, `agents/auto_send.py`, `tools/hubspot_client.py`, `tools/email_sender.py`.

## 4. Tech stack

- Python 3.11+
- Claude API (Sonnet 4.6) via `anthropic` SDK with prompt caching for brand voice
- Postgres on Supabase (state); SQLite for local dev
- SQLAlchemy + Alembic
- Resend for outbound transactional email (already wired)
- Streamlit for the panel (already wired)
- GitHub Actions cron for the daily orchestrator (matches existing `auto-outreach.yml`)
- Gmail OAuth (`gmail.readonly`, `gmail.compose`, `gmail.modify`)
- HubSpot Private App token (existing)
- Moloni API key (Phase 3)

## 5. Brand voice

Drafts must mirror Perry's voice. Source: `config/reps.yaml` (sample messages, tone notes) + `agents/writer.py` `BRAND_CONTEXT`. Add a post-generation validator that rejects drafts violating the rules already encoded in the writer prompt (no em dashes, no AI-tells, MOM uppercase, etc.). Re-roll up to 2× before flagging for human edit.

## 6. Phasing

Ship-fast cadence (the existing Sales Agent was built in ~12 h; this OS extends it, not rebuild):

| Phase | Scope | Target |
|---|---|---|
| **1** | Inbox triage + draft replies + daily Brief + Streamlit panel + HubSpot writeback | Day 1–2 |
| **2** | Outbound + follow-ups + tasting booking (extends `auto_send.py`) | Day 3 |
| **3** | Orders + production handoff + Moloni invoicing + AR | Day 4 |
| **4** | Event requests + capacity check + production brief | Day 5 |

Each phase ships behind a feature flag in the panel so Perry can flip new modules on as they stabilize. No hard 30-day soak gate — confidence is gauged per-phase from accept-rate of the first 20 outputs.

## 7. Daily run sequence (Phase 1)

1. Cron fires 06:00 Lisbon time.
2. Pull last 24 h Gmail threads.
3. Classify each: `NEEDS_REPLY | FYI | NOISE | CALENDAR | INVOICE | INTERNAL`.
4. For `NEEDS_REPLY`, generate draft, run brand-voice validator, write to Gmail Drafts.
5. Pull last 24 h Granola meeting notes; extract commitments + action items.
6. Pull next 7 days Calendar; flag events needing prep.
7. Cross-reference: any commitment older than 14 days with no closing action → surface as stale.
8. Persist to Postgres (`threads`, `tasks`, `commitments`, `daily_runs`).
9. Render daily Brief, send via Resend to `perry@mom-wow.com`.
10. Total wall-clock budget: under 5 minutes.

## 8. State schema (Phase 1 minimum)

- `threads(thread_id, subject, classification, last_action_at, deadline, owner, status)`
- `tasks(id, source, source_id, title, due_at, status, created_at)`
- `commitments(id, source_thread_id, quote, target_date, status)`
- `daily_runs(id, run_date, threads_processed, drafts_created, items_surfaced, errors_json)`

## 9. Decisions (confirmed by Perry, 2026-05-03)

1. **Approval gate** — manual approval is the default; the panel exposes an **AUTO_SEND** toggle Perry can flip on whenever he wants drafts to go out without review. Per-thread override also available.
2. **HubSpot writeback** — yes, push CRM corrections in Phase 1. Reality reconciliation writes back to HubSpot (stage corrections, missing close dates, owner fixes).
3. **Languages** — auto-detect EN and PT on inbound thread; drafter mirrors the language. Default to EN if ambiguous.
4. **GDPR retention** — keep indefinitely while legitimate-interest applies (active or reactivatable B2B relationship). No automatic pruning. Honour right-to-erasure on request via a panel action that wipes the contact across HubSpot + Postgres.
5. **Granola** — already integrated; required for Phase 1 commitment extraction.

## 10. Success metrics (track from Phase 1 go-live)

- 90% of `NEEDS_REPLY` threads have a usable draft by 09:00 Lisbon
- Founder spends ≤15 min/day on triage (down from 60+)
- Zero dropped commitments over a 30-day rolling window
- 80%+ draft acceptance rate after the first 20 drafts
- Daily Brief opened within 2 h of send, 95% of days

## 11. Out of scope

Mobile app, real-time chat UI, automated phone calls, marketing campaign automation, public-facing client portal, inventory management beyond the production sheet, accounting beyond invoice issuance.

## 12. Deliverables

1. All modules above, with unit tests for connectors and snapshot tests for drafter output.
2. Alembic migrations for the state schema.
3. `.github/workflows/sales_os.yml` cron workflow.
4. Streamlit panel deployed to the existing `mom-sales.streamlit.app`.
5. `README_SALES_OS.md` covering setup, secrets, local dev, and rollout.

## 13. Build sequence checklist

**Day 1**
1. Inventory existing repo; map reused vs new.
2. Stand up Supabase Postgres + Alembic; create Phase 1 schema.
3. Build `connectors/gmail.py`, `connectors/calendar.py`, `connectors/granola.py` (Granola already integrated, wrap it).
4. Build `brain/triage.py` with EN/PT detection; dry-run against last 7 days of inbox.

**Day 2**
5. Build `brain/drafter.py` + brand-voice validator (mirrors language of inbound).
6. Build `brain/synthesizer.py` for cross-source commitment extraction.
7. Build `reports/daily_brief.py`; send first test brief to `perry@mom-wow.com`.
8. Build minimum `panel/` (Today, Drafts, Settings with `AUTO_SEND` toggle).
9. Wire `orchestrator/run_daily.py`; deploy GH Actions cron.

**Day 3**: Phase 2 — outbound + follow-ups, tasting booking.
**Day 4**: Phase 3 — orders, production handoff, Moloni invoicing, AR chaser.
**Day 5**: Phase 4 — event intake, capacity check, production brief.

Each phase ends with a 20-output sample review by Perry; if accept-rate ≥80%, flip the feature flag on for autonomous use.
