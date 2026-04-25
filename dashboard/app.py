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
from tools.outreach_queue import load_queue, clear_queue

st.set_page_config(
    page_title="mom-wow Sales Agent",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Brand CSS ──────────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
  /* Sidebar */
  [data-testid="stSidebar"] {{
    background-color: {GREEN_DARK};
  }}
  [data-testid="stSidebar"] * {{
    color: {WHITE} !important;
  }}
  [data-testid="stSidebar"] .stRadio label {{
    color: {WHITE} !important;
  }}
  [data-testid="stSidebar"] .stSelectbox label,
  [data-testid="stSidebar"] .stSelectbox div {{
    color: {WHITE} !important;
  }}

  /* Primary buttons */
  .stButton > button[kind="primary"] {{
    background-color: {GREEN};
    color: {WHITE};
    border: none;
    border-radius: 6px;
    font-weight: 600;
  }}
  .stButton > button[kind="primary"]:hover {{
    background-color: {GREEN_DARK};
    color: {WHITE};
  }}

  /* Stop / danger button override — "replied" buttons */
  .stButton > button[kind="primary"][data-testid*="replied"] {{
    background-color: {TERRACOTTA};
  }}

  /* Metric cards */
  [data-testid="stMetric"] {{
    background-color: {CREAM};
    border-left: 4px solid {GREEN};
    padding: 12px 16px;
    border-radius: 6px;
  }}

  /* Expanders */
  [data-testid="stExpander"] {{
    border-left: 3px solid {GREEN};
    background-color: {CREAM};
  }}

  /* Page title accent */
  h1 {{ color: {GREEN_DARK}; }}
  h2, h3 {{ color: {GREEN}; }}

  /* Warning banner override for "replied" alert */
  .element-container .stAlert[data-baseweb="notification"] {{
    border-left: 4px solid {TERRACOTTA};
  }}

  /* Divider colour */
  hr {{ border-color: {GREEN}22; }}
</style>
""", unsafe_allow_html=True)

ICP = load_icp()
STAGE_LABELS = {s["id"]: s["label"] for s in ICP["pipeline_stages"]}


# ── Sidebar ────────────────────────────────────────────────────────────────────

def sidebar():
    st.sidebar.image("https://via.placeholder.com/200x60?text=mom-wow", use_column_width=True)
    st.sidebar.title("🍋 Sales Agent")

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
    st.title("📊 Pipeline")
    st.caption("Live data from HubSpot")

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
    rows = []
    for d in deals:
        props = d.get("properties", {})
        rows.append({
            "Deal": props.get("dealname", ""),
            "Stage": STAGE_LABELS.get(props.get("dealstage", ""), props.get("dealstage", "")),
            "Value (€/mo)": float(props.get("amount") or 0),
            "Rep": props.get("hubspot_owner_id", ""),
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
        st.image(str(funnel_path), use_column_width=True)
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
    st.title(f"📬 Daily Queue — {rep_name}")
    st.caption(f"Messages ready to send today ({date.today().isoformat()})")

    items = load_queue(rep_id)

    if not items:
        st.success("Queue is empty — all caught up! 🎉")
        return

    st.info(f"{len(items)} messages to send today. Takes ~10 mins on LinkedIn.")

    st.warning(
        "⚠️ **Did someone reply?** If a prospect responds via ANY channel "
        "(LinkedIn, email, WhatsApp, phone, in person) — click **'They Replied'** immediately. "
        "This stops ALL automated follow-ups so you don't annoy them.",
        icon="🛑",
    )
    st.divider()

    for i, item in enumerate(items, 1):
        with st.expander(f"[{i}] {item.get('venue_name')} — {item.get('message_type')}"):
            col1, col2 = st.columns([1, 3])
            with col1:
                st.markdown(f"**Channel:** {item.get('channel')}")
                if item.get("linkedin_url"):
                    st.markdown(f"[Open LinkedIn Profile]({item['linkedin_url']})")
                if item.get("contact_name"):
                    st.markdown(f"**Contact:** {item['contact_name']}")

                st.divider()
                st.markdown("**They responded?**")
                reply_channel = st.selectbox(
                    "Via which channel?",
                    ["LinkedIn", "Email", "WhatsApp", "Phone", "In person", "Other"],
                    key=f"reply_channel_{i}",
                )
                contact_id = item.get("contact_id", "")
                deal_id = item.get("deal_id", "")
                if st.button(
                    "🛑 They Replied — Stop All Follow-ups",
                    key=f"replied_{i}",
                    type="primary",
                    disabled=not deal_id,
                ):
                    try:
                        from agents.crm import mark_replied
                        mark_replied(deal_id, contact_id, channel=reply_channel)
                        st.success(f"✅ All automated messages stopped for {item.get('venue_name')}. You're in the driver's seat now.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
            with col2:
                st.text_area(
                    "Message (copy-paste ready)",
                    value=item.get("message", ""),
                    height=150,
                    key=f"msg_{i}",
                )

    if st.button("🗑️ Clear Today's Queue", type="secondary"):
        clear_queue(rep_id)
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
        new_title = st.text_input("Title", "Sales Executive, mom-wow")
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
            with st.spinner("Sending email..."):
                try:
                    from agents.reporter import send_friday_report
                    send_friday_report()
                    st.success("Email sent!")
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
                st.image(str(path), use_column_width=True)


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
