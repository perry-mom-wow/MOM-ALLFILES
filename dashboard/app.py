"""Streamlit dashboard — pipeline, team management, daily queues, reports."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on path when running via `streamlit run dashboard/app.py`
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
from datetime import date

import streamlit as st
import plotly.graph_objects as go

from config.settings import load_reps, save_reps, load_icp
from config.brand import GREEN, GREEN_DARK, WHITE, BLACK, CREAM, PINK, TERRACOTTA, ORANGE, MUSTARD, BLUE_LIGHT
from tools.outreach_queue import (
    load_queue, clear_queue, remove_from_queue,
    load_pending, log_sent, remove_pending_item,
)

_LOGO = Path(__file__).parent.parent / "static" / "mom-logo.png"

_BRAND_FONT_FACES = """
@font-face {
  font-family: 'ABC Favorit';
  src: url('app/static/fonts/ABCFavorit-Regular.otf') format('opentype');
  font-weight: 400; font-style: normal; font-display: swap;
}
@font-face {
  font-family: 'ABC Favorit Mono';
  src: url('app/static/fonts/ABCFavoritMono-Light.otf') format('opentype');
  font-weight: 300; font-style: normal; font-display: swap;
}
"""
st.set_page_config(
    page_title="MOM · Sales Agent",
    page_icon="🍄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Brand CSS ──────────────────────────────────────────────────────────────────
st.markdown(f"""<style>
  @import url('https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300;0,9..144,400;0,9..144,500;0,9..144,600;1,9..144,400&display=swap');
  {_BRAND_FONT_FACES}

  /* ── Typography ──────────────────────────────────────────────── */
  html, body, [class*="css"], .stApp, .stMarkdown, p, span, div, label {{
    font-family: 'ABC Favorit', -apple-system, BlinkMacSystemFont, sans-serif !important;
    letter-spacing: 0.005em;
  }}
  /* Restore Material Icons font so expander arrows etc. render as glyphs, not text */
  .material-icons, .material-symbols-outlined, .material-symbols-rounded,
  .material-symbols-sharp, [class*="material-icons"], [class*="material-symbols"],
  [data-testid="stExpanderToggleIcon"], [data-testid="stIconMaterial"] {{
    font-family: 'Material Symbols Outlined', 'Material Icons', 'Material Symbols Rounded' !important;
    font-feature-settings: 'liga';
    -webkit-font-feature-settings: 'liga';
  }}
  code, pre, kbd, samp {{
    font-family: 'ABC Favorit Mono', monospace !important;
  }}
  h1, h2, h3, h4, h5, h6,
  [data-testid="stMetricValue"] {{
    font-family: 'Fraunces', Georgia, serif !important;
    font-weight: 400 !important;
    letter-spacing: -0.015em;
    font-variation-settings: "opsz" 96, "SOFT" 50;
  }}
  h1 {{ color: {GREEN_DARK}; font-size: 2.6rem !important; font-weight: 600 !important; }}
  h2, h3 {{ color: {GREEN}; }}
  [data-testid="stMetricValue"] {{
    color: {GREEN_DARK};
    font-size: 2rem !important;
  }}
  [data-testid="stMetricLabel"] {{
    font-family: 'ABC Favorit', sans-serif !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.72rem !important;
    font-weight: 600;
    color: {GREEN_DARK}99;
  }}

  /* ── Sidebar ─────────────────────────────────────────────────── */
  [data-testid="stSidebar"] {{
    background: linear-gradient(180deg, {GREEN_DARK} 0%, #2D3D24 100%);
    padding-top: 0.5rem;
  }}
  [data-testid="stSidebar"] * {{ color: {WHITE} !important; }}
  [data-testid="stSidebar"] .stRadio label,
  [data-testid="stSidebar"] .stSelectbox label,
  [data-testid="stSidebar"] .stSelectbox div {{ color: {WHITE} !important; }}
  [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {{
    color: {CREAM} !important;
    font-family: 'Fraunces', Georgia, serif !important;
    font-weight: 400 !important;
    letter-spacing: 0.04em;
  }}
  [data-testid="stSidebar"] .stRadio > div {{
    gap: 0.25rem;
  }}
  [data-testid="stSidebar"] .stRadio label {{
    padding: 0.45rem 0.6rem;
    border-radius: 6px;
    transition: background 0.15s ease;
    font-size: 0.95rem;
  }}
  [data-testid="stSidebar"] .stRadio label:hover {{
    background-color: {GREEN}33;
  }}
  /* Sidebar logo container — keep tight, no extra background box */
  [data-testid="stSidebar"] [data-testid="stImage"] {{
    background-color: {CREAM};
    border-radius: 10px;
    padding: 1rem;
    margin: 0.5rem 0 1rem 0;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }}
  [data-testid="stSidebar"] [data-testid="stImage"] img {{
    max-width: 80%;
    height: auto;
    display: block;
    margin: 0 auto;
  }}
  .sidebar-tagline {{
    text-align: center;
    color: {CREAM}cc !important;
    font-family: 'Fraunces', serif !important;
    font-style: italic;
    font-size: 0.95rem;
    margin-top: -0.25rem;
    letter-spacing: 0.05em;
  }}

  /* ── Buttons ─────────────────────────────────────────────────── */
  .stButton > button[kind="primary"] {{
    background-color: {GREEN};
    color: {WHITE};
    border: none;
    border-radius: 999px;
    font-weight: 500;
    font-family: 'ABC Favorit', sans-serif !important;
    letter-spacing: 0.03em;
    padding: 0.5rem 1.2rem;
    transition: all 0.15s ease;
  }}
  .stButton > button[kind="primary"]:hover {{
    background-color: {GREEN_DARK};
    color: {WHITE};
    transform: translateY(-1px);
    box-shadow: 0 4px 12px {GREEN_DARK}33;
  }}
  .stButton > button[kind="primary"][data-testid*="replied"] {{
    background-color: {TERRACOTTA};
  }}

  /* ── Metric cards ────────────────────────────────────────────── */
  [data-testid="stMetric"] {{
    background-color: {CREAM};
    border-left: 4px solid {GREEN};
    padding: 14px 18px;
    border-radius: 8px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  }}

  /* ── Expanders ───────────────────────────────────────────────── */
  [data-testid="stExpander"] {{
    border: 1px solid {GREEN}22;
    border-left: 3px solid {GREEN};
    background-color: {CREAM};
    border-radius: 6px;
  }}

  /* ── Misc ───────────────────────────────────────────────────── */
  .element-container .stAlert[data-baseweb="notification"] {{
    border-left: 4px solid {TERRACOTTA};
  }}
  hr {{ border-color: {GREEN}22; }}
  .stCaption, [data-testid="stCaptionContainer"] {{
    font-style: italic;
    color: {GREEN_DARK}99 !important;
  }}
</style>
""", unsafe_allow_html=True)

ICP = load_icp()
STAGE_LABELS = {s["id"]: s["label"] for s in ICP["pipeline_stages"]}


# ── Sidebar ────────────────────────────────────────────────────────────────────

LOGO_PATH = _LOGO


def sidebar():
    if LOGO_PATH.exists():
        st.sidebar.image(str(LOGO_PATH), use_container_width=True)
    else:
        st.sidebar.markdown(f"<h1 style='text-align:center;color:{CREAM};'>MOM</h1>", unsafe_allow_html=True)
    st.sidebar.markdown(
        '<div class="sidebar-tagline">Sales Agent</div>',
        unsafe_allow_html=True,
    )

    page = st.sidebar.radio(
        "Navigate",
        ["Pipeline", "Daily Queue", "Run Agent", "Team", "Reports"],
        label_visibility="collapsed",
    )

    st.sidebar.divider()
    reps = load_reps()
    rep_options = {r["name"]: r["id"] for r in reps}
    active_rep_name = st.sidebar.selectbox("👤 Active Rep", list(rep_options.keys()))
    active_rep_id = rep_options[active_rep_name]
    st.session_state["active_rep_id"] = active_rep_id
    st.session_state["active_rep_name"] = active_rep_name

    return page


# ── Pipeline page ──────────────────────────────────────────────────────────────

def page_pipeline():
    title_col, btn_col = st.columns([4, 1])
    with title_col:
        st.title("📊 Pipeline")
        st.caption("Live data from HubSpot")
    with btn_col:
        st.markdown("<div style='height:1.5rem'></div>", unsafe_allow_html=True)
        if st.button("🧹 Clean CRM", help="Find and remove junk + duplicate deals"):
            with st.spinner("Scanning HubSpot..."):
                try:
                    from agents.cleanup import cleanup
                    result = cleanup(dry_run=False)
                    if result["deleted"]:
                        st.success(f"Deleted {result['deleted']} deals.")
                        with st.expander("What was removed"):
                            for j in result["junk"]:
                                st.markdown(f"- **{j['name']}** — _{j['reason']}_")
                    else:
                        st.info("CRM is already clean ✨")
                except Exception as e:
                    st.error(f"Error: {e}")

    try:
        from tools import hubspot_client as hs
        deals = hs.get_all_deals()
    except Exception as e:
        st.warning(f"Could not connect to HubSpot: {e}")
        deals = []

    if not deals:
        st.info("No deals in HubSpot yet. Run the agent to discover prospects.")
        return

    import pandas as pd
    from agents.reporter import _HS_TO_OURS
    import re
    rows = []
    for d in deals:
        props = d.get("properties", {})
        hs_stage = (props.get("dealstage") or "").lower()
        our_stage = _HS_TO_OURS.get(hs_stage, hs_stage)
        deal_name = props.get("dealname", "")
        # Rep ID is encoded as "[rep_id]" suffix in the deal name during onboarding.
        rep_match = re.search(r"\[([^\]]+)\]\s*$", deal_name)
        rep = rep_match.group(1) if rep_match else (props.get("hubspot_owner_id") or "")
        rows.append({
            "Deal": deal_name,
            "Stage": STAGE_LABELS.get(our_stage, our_stage),
            "Value (€/mo)": float(props.get("amount") or 0),
            "Rep": rep,
            "Next Follow-up": (props.get("closedate") or "")[:10],
        })
    df = pd.DataFrame(rows)

    # KPI row
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Deals", len(df))
    c2.metric("Pipeline Value", f"€{df['Value (€/mo)'].sum():,.0f}/mo")
    c3.metric("Won", len(df[df["Stage"] == "Won"]))
    c4.metric("Nurture", len(df[df["Stage"] == "Nurture"]))

    st.divider()

    # Funnel chart
    try:
        from agents.reporter import generate_report, _make_funnel_chart, _deals_to_df
        deal_df = _deals_to_df(deals)
        funnel_path = _make_funnel_chart(deal_df)
        st.image(str(funnel_path), use_container_width=True)
    except Exception as e:
        st.warning(f"Chart error: {e}")

    # Deals table
    st.subheader("All Deals")
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("🛑 Mark a Deal as Replied")
    st.caption("Use this if a prospect responds via any channel — stops all automated messages immediately.")
    with st.form("mark_replied_form"):
        deal_name_input = st.text_input("Deal name (or partial)", placeholder="e.g. Beach Club Algarve")
        reply_ch = st.selectbox("Channel they responded via", ["LinkedIn", "Email", "WhatsApp", "Phone", "In person", "Other"])
        contact_id_input = st.text_input("HubSpot Contact ID (from HubSpot URL)", placeholder="Optional")
        submitted = st.form_submit_button("🛑 Stop Follow-ups for This Deal", type="primary")
        if submitted and deal_name_input:
            matching = [d for d in deals if deal_name_input.lower() in (d.get("properties", {}).get("dealname") or "").lower()]
            if not matching:
                st.error("No matching deal found.")
            else:
                for d in matching:
                    deal_id = d.get("id")
                    try:
                        from agents.crm import mark_replied
                        mark_replied(deal_id, contact_id_input or "unknown", channel=reply_ch)
                        st.success(f"✅ Stopped all follow-ups for: {d.get('properties', {}).get('dealname')}")
                    except Exception as e:
                        st.error(f"Error: {e}")


# ── Daily Queue page ───────────────────────────────────────────────────────────

def page_queue():
    rep_id = st.session_state.get("active_rep_id", "marcus")
    rep_name = st.session_state.get("active_rep_name", "Rep")

    items = load_pending(rep_id)
    total = len(items)
    today_iso = date.today().isoformat()
    carryover = sum(1 for i in items if i.get("_source_date") != today_iso)

    st.title(f"Daily Queue — {rep_name}")

    if not items:
        st.success("Queue is empty — all caught up!")
        return

    if carryover:
        st.info(f"{carryover} message(s) carried over from previous days.")

    # Track position in queue; reset if queue shrank (item was removed)
    if "queue_index" not in st.session_state or st.session_state.queue_index >= total:
        st.session_state.queue_index = 0

    idx = st.session_state.queue_index
    item = items[idx]

    # ── Progress bar ──────────────────────────────────────────────────────────
    done = idx  # items already marked sent this session
    st.caption(f"Message {idx + 1} of {total}")
    st.progress(idx / total)

    st.divider()

    # ── Main card ─────────────────────────────────────────────────────────────
    venue = item.get("venue_name", "Unknown")
    msg_type = item.get("message_type", "")
    channel = item.get("channel", "LinkedIn")
    contact_name = item.get("contact_name") or ""
    contact_title = item.get("contact_title") or ""
    linkedin_url = item.get("linkedin_url") or ""
    email = item.get("email") or ""
    message = item.get("message", "")
    deal_id = item.get("deal_id", "")
    contact_id = item.get("contact_id", "")

    st.subheader(venue)
    meta_parts = [msg_type, channel]
    if contact_name:
        meta_parts.append(contact_name)
    if contact_title:
        meta_parts.append(contact_title)
    src_date = item.get("_source_date")
    if src_date and src_date != today_iso:
        meta_parts.append(f"queued {src_date}")
    st.caption("  ·  ".join(meta_parts))

    # Contact links
    link_cols = st.columns(4)
    col_i = 0
    if linkedin_url:
        link_cols[col_i].link_button("Open LinkedIn", linkedin_url, type="primary")
        col_i += 1
    if email:
        link_cols[col_i].link_button("Open Email", f"mailto:{email}")
        col_i += 1

    st.divider()

    # Message — st.code gives a built-in copy button
    st.markdown("**Message** — click the copy icon top-right to copy")
    st.code(message, language=None, wrap_lines=True)

    st.divider()

    # ── Actions ───────────────────────────────────────────────────────────────
    action_cols = st.columns([2, 1, 2])

    with action_cols[0]:
        if st.button("✅  Sent — Next", type="primary", use_container_width=True):
            if deal_id:
                try:
                    from tools import hubspot_client as hs
                    hs.update_deal_stage(deal_id, "contacted")
                except Exception as e:
                    st.warning(f"Couldn't update HubSpot stage: {e}")
            log_sent(rep_id, item)
            remove_pending_item(rep_id, item)
            if idx >= total - 1:
                st.session_state.queue_index = max(0, total - 2)
            st.rerun()

    with action_cols[1]:
        if st.button("Skip", use_container_width=True):
            st.session_state.queue_index = (idx + 1) % total
            st.rerun()

    with action_cols[2]:
        with st.popover("🛑 They Replied", use_container_width=True):
            st.markdown(f"**Stop all follow-ups for {venue}?**")
            st.caption("Use this if they reply via LinkedIn, email, WhatsApp, phone, or in person.")
            reply_channel = st.selectbox(
                "Channel they replied on",
                ["LinkedIn", "Email", "WhatsApp", "Phone", "In person", "Other"],
                key="reply_channel_popover",
            )
            if st.button("Confirm — Stop All Follow-ups", type="primary", disabled=not deal_id):
                try:
                    from agents.crm import mark_replied
                    mark_replied(deal_id, contact_id, channel=reply_channel)
                    st.success(f"Stopped all follow-ups for {venue}.")
                    st.session_state.queue_index = max(0, idx - 1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

    st.divider()

    # ── Remaining list (collapsed) ────────────────────────────────────────────
    remaining = [it for j, it in enumerate(items) if j != idx]
    if remaining:
        with st.expander(f"See remaining {len(remaining)} messages"):
            for j, it in enumerate(remaining):
                actual_j = j if j < idx else j + 1
                st.markdown(
                    f"**{actual_j + 1}.** {it.get('venue_name')} — "
                    f"{it.get('message_type')} · {it.get('channel')}"
                )

    st.divider()
    if st.button("Clear today's queue file", type="secondary",
                 help="Only deletes today's queue file. Carryover items from previous days stay."):
        clear_queue(rep_id)
        st.session_state.queue_index = 0
        st.rerun()


# ── Run Agent page ─────────────────────────────────────────────────────────────

def page_run_agent():
    st.title("🤖 Run Sales Agent")
    rep_id = st.session_state.get("active_rep_id", "marcus")
    rep_name = st.session_state.get("active_rep_name", "Rep")
    st.info(f"Messages will be prepared for **{rep_name}**. Switch rep in the sidebar.")

    tab1, tab2 = st.tabs(["Discover Prospects", "Run Follow-ups"])

    with tab1:
        st.subheader("Discover New Prospects")
        location = st.text_input("Location (city/area)", placeholder="e.g. Lisboa, Algarve, Cascais")
        venue_types = st.multiselect(
            "Venue Types",
            ["beach_club", "restaurant", "cafe", "hotel", "gym", "wellness_center", "spa"],
            default=["beach_club", "restaurant", "hotel"],
        )
        max_per = st.slider("Max prospects per venue type", 5, 30, 10)

        if st.button("🔍 Discover & Onboard", type="primary", disabled=not location):
            with st.spinner("Discovering prospects..."):
                try:
                    from agents.discovery import discover_prospects
                    prospects = discover_prospects(location, venue_types, max_per_type=max_per)
                    st.success(f"Found {len(prospects)} prospects.")

                    results = []
                    progress = st.progress(0)
                    for idx, raw in enumerate(prospects):
                        try:
                            from agents.researcher import research_prospect
                            profile = research_prospect(raw)

                            from agents.writer import generate_sequence
                            sequence = generate_sequence(profile, rep_id)

                            from agents.crm import onboard_prospect
                            crm_result = onboard_prospect(profile, sequence, rep_id)

                            results.append({
                                "name": profile.name,
                                "tier": profile.tier,
                                "stage": "Contacted",
                                "revenue_eur": crm_result["revenue_potential_eur"],
                                "next_followup": crm_result["next_followup"],
                            })
                        except Exception as e:
                            results.append({"name": raw.name, "error": str(e)})
                        progress.progress((idx + 1) / len(prospects))

                    import pandas as pd
                    st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)
                    st.info(f"Check the Daily Queue for {rep_name}'s messages to send.")
                except Exception as e:
                    st.error(f"Error: {e}")

    with tab2:
        st.subheader("Run Daily Follow-ups")
        st.write("Check all active deals for due follow-ups and queue messages.")
        if st.button("▶️ Run Sequencer", type="primary"):
            with st.spinner("Running sequencer..."):
                try:
                    from agents.sequencer import run_daily
                    result = run_daily()
                    st.success(f"Done. {result['messages_queued']} messages queued.")
                    st.json(result)
                except Exception as e:
                    st.error(f"Error: {e}")


# ── Team page ──────────────────────────────────────────────────────────────────

def page_team():
    st.title("👥 Team Management")
    reps = load_reps()

    for rep in reps:
        with st.expander(f"{'✅' if rep.get('active', True) else '❌'} {rep['name']} — {rep['title']}"):
            col1, col2 = st.columns(2)
            with col1:
                rep["name"] = st.text_input("Name", rep["name"], key=f"name_{rep['id']}")
                rep["title"] = st.text_input("Title", rep["title"], key=f"title_{rep['id']}")
                rep["email"] = st.text_input("Email", rep.get("email", ""), key=f"email_{rep['id']}")
            with col2:
                rep["linkedin_url"] = st.text_input("LinkedIn URL", rep.get("linkedin_url", ""), key=f"li_{rep['id']}")
                rep["tone_notes"] = st.text_area(
                    "Voice & tone notes",
                    rep.get("tone_notes", ""),
                    height=100,
                    key=f"tone_{rep['id']}",
                )
            samples_raw = st.text_area(
                "Sample messages (one per line — paste actual messages they've written)",
                "\n".join(rep.get("sample_messages", [])),
                height=120,
                key=f"samples_{rep['id']}",
            )
            rep["sample_messages"] = [s.strip() for s in samples_raw.split("\n") if s.strip()]
            rep["active"] = st.toggle("Active", rep.get("active", True), key=f"active_{rep['id']}")

    if st.button("💾 Save Team", type="primary"):
        save_reps(reps)
        st.success("Team saved!")

    st.divider()
    st.subheader("➕ Add New Rep")
    with st.form("add_rep"):
        new_name = st.text_input("Name")
        new_title = st.text_input("Title", "Sales Executive, MOM")
        new_email = st.text_input("Email")
        new_linkedin = st.text_input("LinkedIn URL")
        new_tone = st.text_area("Voice & tone notes", height=80)
        new_samples = st.text_area("Sample messages (one per line)", height=100)

        submitted = st.form_submit_button("Add Rep")
        if submitted and new_name:
            new_id = new_name.lower().replace(" ", "_")
            new_rep = {
                "id": new_id,
                "name": new_name,
                "title": new_title,
                "email": new_email,
                "linkedin_url": new_linkedin,
                "tone_notes": new_tone,
                "sample_messages": [s.strip() for s in new_samples.split("\n") if s.strip()],
                "active": True,
            }
            reps.append(new_rep)
            save_reps(reps)
            st.success(f"✅ {new_name} added to the team!")
            st.rerun()


# ── Reports page ───────────────────────────────────────────────────────────────

def page_reports():
    st.title("📈 Reports")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("📊 Generate Report Now", type="primary"):
            with st.spinner("Generating report..."):
                try:
                    from agents.reporter import generate_report
                    report = generate_report()
                    st.session_state["last_report"] = report
                    st.success("Report generated!")
                except Exception as e:
                    st.error(f"Error: {e}")

    with col2:
        if st.button("📧 Send Friday Email Now"):
            with st.spinner("Running cleanup + sending email..."):
                try:
                    from agents.reporter import send_friday_report
                    send_friday_report()
                    st.success("Cleanup ran and email sent!")
                except Exception as e:
                    st.error(f"Error: {e}")

    report = st.session_state.get("last_report")
    if report:
        st.divider()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Deals", report["total_deals"])
        c2.metric("Pipeline", f"€{report['pipeline_value_eur']:,.0f}/mo")
        c3.metric("Won", report["won"])
        c4.metric("Nurture", report["nurture_count"])

        st.subheader("🎯 Top 3 Prospects")
        for p in report["top3_prospects"]:
            st.markdown(f"- {p}")

        st.subheader("Charts")
        cols = st.columns(2)
        for i, path in enumerate(report.get("chart_paths", [])):
            with cols[i % 2]:
                st.image(str(path), use_container_width=True)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    page = sidebar()

    if page == "Pipeline":
        page_pipeline()
    elif page == "Daily Queue":
        page_queue()
    elif page == "Run Agent":
        page_run_agent()
    elif page == "Team":
        page_team()
    elif page == "Reports":
        page_reports()


if __name__ == "__main__":
    main()
