"""Streamlit dashboard — pipeline, team management, daily queues, reports."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on path when running via `streamlit run dashboard/app.py`
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
from datetime import date, datetime

import streamlit as st


def _format_eu_date(raw) -> str:
    """Format an ISO date/datetime string as DD.MM.YYYY for display.

    Accepts: '2026-05-01', '2026-05-01T10:08:08.094Z', None, or empty string.
    Returns: '01.05.2026', or '' if input is missing/unparseable.
    """
    if not raw:
        return ""
    s = str(raw)[:10]  # take just the date portion
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
        return d.strftime("%d.%m.%Y")
    except Exception:
        return s
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
        ["Pipeline", "Daily Queue", "Inbound", "Run Agent", "Team", "Reports"],
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
    title_col, batch_col, clean_col = st.columns([3, 1, 1])
    with title_col:
        st.title("📊 Pipeline")
        st.caption("Live data from HubSpot")
    with batch_col:
        st.markdown("<div style='height:1.5rem'></div>", unsafe_allow_html=True)
        if st.button("📨 Send my batch", help="Auto-send today's cold-email batch (preview first)", use_container_width=True):
            with st.spinner("Building today's batch..."):
                from agents.auto_send import _candidates_for_today
                st.session_state["batch_items"] = _candidates_for_today()
                st.session_state["batch_skip"] = set()
    with clean_col:
        st.markdown("<div style='height:1.5rem'></div>", unsafe_allow_html=True)
        if st.button("🧹 Clean CRM", help="Find and remove junk + duplicate deals", use_container_width=True):
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

    # ── Auto-send preview panel ──
    if "batch_items" in st.session_state:
        items = st.session_state["batch_items"]
        skip = st.session_state.get("batch_skip", set())
        with st.container(border=True):
            if not items:
                st.info("No uncontacted prospects with a ready cold email. Run discovery first.")
            else:
                included = [it for it in items if it["deal_id"] not in skip]
                from config.settings import PORTFOLIO_URL
                portfolio_note = (
                    f"Portfolio P.S. **on** → {PORTFOLIO_URL}"
                    if PORTFOLIO_URL else
                    "Portfolio P.S. **off** (no `PORTFOLIO_URL` set yet — placeholder)."
                )
                st.markdown(f"### Preview · {len(included)} of {len(items)} ready to send")
                st.caption(portfolio_note)
                for it in items:
                    deal_id = it["deal_id"]
                    is_skipped = deal_id in skip
                    cols = st.columns([6, 1])
                    with cols[0]:
                        with st.expander(f"{'❌ ' if is_skipped else '✓ '}{it['prospect_name']} → {it['contact_email']}"):
                            st.markdown(f"**Subject:** {it['subject']}")
                            st.markdown(it["body"].replace("\n", "  \n"))
                            if PORTFOLIO_URL:
                                st.caption(f"_(P.S. with portfolio link: {PORTFOLIO_URL})_")
                    with cols[1]:
                        label = "Include" if is_skipped else "Skip"
                        if st.button(label, key=f"toggle_{deal_id}"):
                            if is_skipped:
                                skip.discard(deal_id)
                            else:
                                skip.add(deal_id)
                            st.session_state["batch_skip"] = skip
                            st.rerun()

                send_col, cancel_col = st.columns([1, 1])
                with send_col:
                    if st.button(f"✉️ Send {len(included)} now", type="primary", disabled=not included, use_container_width=True):
                        with st.spinner("Sending..."):
                            from agents.auto_send import send_selected
                            result = send_selected(included)
                        st.success(f"Sent {result['sent']}/{result['total']}.")
                        for r in result["results"]:
                            if r["sent"]:
                                st.markdown(f"- ✓ **{r['prospect_name']}** → {r['to']}")
                            else:
                                st.markdown(f"- ✗ **{r['prospect_name']}** → {r['to']} _({r.get('error')})_")
                        del st.session_state["batch_items"]
                        st.session_state.pop("batch_skip", None)
                with cancel_col:
                    if st.button("Cancel", use_container_width=True):
                        del st.session_state["batch_items"]
                        st.session_state.pop("batch_skip", None)
                        st.rerun()

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

    # Resolve HubSpot owner IDs → human names once per session (10-min TTL).
    @st.cache_data(ttl=600, show_spinner=False)
    def _owner_map() -> dict[str, str]:
        try:
            return hs.get_owners()
        except Exception:
            return {}
    owners = _owner_map()

    # rep_id ("perry_patraszewski") → first name ("Perry"), built from reps.yaml.
    rep_first_name = {r["id"]: (r.get("name") or r["id"]).split()[0].capitalize()
                      for r in load_reps()}

    def _first_name(s: str) -> str:
        """Take the first whitespace-separated token and capitalize it."""
        if not s:
            return ""
        return s.strip().split()[0].capitalize()

    rows = []
    for d in deals:
        props = d.get("properties", {})
        hs_stage = (props.get("dealstage") or "").lower()
        our_stage = _HS_TO_OURS.get(hs_stage, hs_stage)
        deal_name = props.get("dealname", "")
        # Rep priority: [rep_id] suffix in deal name → HubSpot owner name → raw owner ID.
        rep_match = re.search(r"\[([^\]]+)\]\s*$", deal_name)
        owner_id = (props.get("hubspot_owner_id") or "").strip()
        if rep_match:
            rid = rep_match.group(1).strip()
            # Prefer the rep's display name from reps.yaml; otherwise capitalize the id's first token.
            rep = rep_first_name.get(rid) or _first_name(rid.replace("_", " "))
        elif owner_id and owner_id in owners:
            rep = _first_name(owners[owner_id])
        else:
            rep = owner_id
        rows.append({
            "Deal": deal_name,
            "Stage": STAGE_LABELS.get(our_stage, our_stage),
            "Value (€/mo)": float(props.get("amount") or 0),
            "Rep": rep,
            "Next Follow-up": _format_eu_date(props.get("closedate")),
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

    # ── Auto-swap to email when there is no LinkedIn profile ─────────────────
    # The queue holds LinkedIn-shaped messages by default. If we have no
    # LinkedIn URL on the prospect, that copy is unsendable — flip the card
    # to show the email opener from the sequence file (subject + body) so
    # Perry can paste the email instead. He can revert the swap by adding a
    # LinkedIn URL via the editor below.
    swapped_to_email = False
    email_subject: str = ""
    if (not linkedin_url) and channel.lower() == "linkedin" and deal_id:
        seq_path = ROOT / "data" / "sequences" / f"{deal_id}.json"
        if seq_path.exists():
            try:
                seq_data = json.loads(seq_path.read_text())
                email_opener = (seq_data.get("messages") or {}).get("email_opener") or {}
                if email_opener.get("body"):
                    swapped_to_email = True
                    msg_type = "Email Opener (LinkedIn unavailable)"
                    channel = "Email"
                    message = email_opener["body"]
                    email_subject = email_opener.get("subject") or f"Quick note from MOM about {venue}"
                    # If the queue item didn't have an email but the sequence
                    # file does, surface it for the contact buttons.
                    if not email and seq_data.get("contact_email"):
                        email = seq_data["contact_email"]
            except Exception:
                pass

    st.subheader(venue)
    if swapped_to_email:
        st.caption(
            "📧 No LinkedIn profile on file — showing **email opener** instead. "
            "Add a LinkedIn URL below to switch back."
        )
    meta_parts = [msg_type, channel]
    if contact_name:
        meta_parts.append(contact_name)
    if contact_title:
        meta_parts.append(contact_title)
    src_date = item.get("_source_date")
    if src_date and src_date != today_iso:
        meta_parts.append(f"queued {_format_eu_date(src_date)}")
    st.caption("  ·  ".join(meta_parts))

    # Contact channels — always render a clickable button per channel.
    # If we have the real link/address: open it.
    # If we don't: open a Google search so the rep can find it in 1 click.
    from urllib.parse import quote_plus

    def _gsearch(q: str) -> str:
        return f"https://www.google.com/search?q={quote_plus(q)}"

    venue_query = venue
    address = item.get("address")
    if address:
        # Use the city/area, not the full street address — broader hit rate.
        city = address.split(",")[-1].strip() or "Lisbon"
        venue_query = f'"{venue}" {city}'
    else:
        venue_query = f'"{venue}" Lisbon'

    phone = item.get("phone")
    instagram = item.get("instagram_handle")

    buttons: list[dict] = []
    if linkedin_url:
        buttons.append({"label": "💼 Open LinkedIn", "url": linkedin_url, "type": "primary"})
    else:
        buttons.append({
            "label": "🔍 Find LinkedIn",
            "url": _gsearch(f"site:linkedin.com {venue_query}"),
            "help": "No LinkedIn on file — opens a Google search",
        })

    if email:
        buttons.append({"label": "✉️ Open Email", "url": f"mailto:{email}"})
    else:
        buttons.append({
            "label": "🔍 Find Email",
            "url": _gsearch(f"{venue_query} contact email"),
            "help": "No email on file — opens a Google search",
        })

    if phone:
        buttons.append({"label": f"📞 {phone}", "url": f"tel:{phone}"})

    if instagram:
        handle = instagram.lstrip("@")
        buttons.append({"label": f"📷 @{handle}", "url": f"https://instagram.com/{handle}"})

    link_cols = st.columns(len(buttons))
    for col, b in zip(link_cols, buttons):
        col.link_button(
            b["label"],
            b["url"],
            type=b.get("type", "secondary"),
            help=b.get("help"),
            use_container_width=True,
        )

    # ── Inline editor: paste back contact info you found via search ──────────
    missing_labels = [
        n for n, v in (
            ("LinkedIn", linkedin_url),
            ("Email", email),
            ("Phone", phone),
            ("Instagram", instagram),
        ) if not v
    ]
    expander_label = (
        f"✏️ Add missing contact info ({', '.join(missing_labels)})"
        if missing_labels else
        "✏️ Update contact info"
    )
    with st.expander(expander_label, expanded=bool(missing_labels)):
        # ── Apply pending auto-find results BEFORE rendering the inputs so
        # st.session_state[<input_key>] is set in time for Streamlit to use it
        # as the input value on this render. ──
        autofind_pending_key = f"_autofind_pending_{deal_id}_{idx}"
        if autofind_pending_key in st.session_state:
            found = st.session_state.pop(autofind_pending_key)
            if found.get("linkedin_url"):
                st.session_state[f"li_{deal_id}_{idx}"] = found["linkedin_url"]
            if found.get("email"):
                st.session_state[f"em_{deal_id}_{idx}"] = found["email"]
            if found.get("phone"):
                st.session_state[f"ph_{deal_id}_{idx}"] = found["phone"]
            if found.get("instagram_handle"):
                st.session_state[f"ig_{deal_id}_{idx}"] = found["instagram_handle"]

        af_col, _ = st.columns([1.5, 4])
        if af_col.button(
            "🔎 Auto-find",
            key=f"autofind_{deal_id}_{idx}",
            help="Run a web search and pre-fill any missing fields. You can still edit before saving.",
            use_container_width=True,
        ):
            from tools.contact_finder import auto_find_contacts
            with st.spinner(f"Searching for {venue}..."):
                try:
                    found = auto_find_contacts(venue, address)
                    found_dict = found.to_dict()
                except Exception as e:
                    found_dict = {}
                    st.error(f"Auto-find failed: {e}")
            actionable = {
                k: v for k, v in found_dict.items()
                if k in ("email", "linkedin_url", "phone", "instagram_handle") and v
            }
            if actionable:
                st.session_state[autofind_pending_key] = actionable
                pretty = ", ".join(actionable.keys())
                st.toast(f"Auto-find filled: {pretty}", icon="✅")
                st.rerun()
            else:
                st.warning("Auto-find found nothing. Try the 🔍 search buttons above.")

        edit_cols = st.columns(2)
        with edit_cols[0]:
            new_linkedin = st.text_input(
                "LinkedIn URL",
                value=linkedin_url or "",
                placeholder="https://linkedin.com/in/...",
                key=f"li_{deal_id}_{idx}",
            )
            new_phone = st.text_input(
                "Phone",
                value=phone or "",
                placeholder="+351 ...",
                key=f"ph_{deal_id}_{idx}",
            )
        with edit_cols[1]:
            new_email = st.text_input(
                "Email",
                value=email or "",
                placeholder="name@venue.com",
                key=f"em_{deal_id}_{idx}",
            )
            new_instagram = st.text_input(
                "Instagram handle",
                value=(instagram or "").lstrip("@"),
                placeholder="venuehandle (no @)",
                key=f"ig_{deal_id}_{idx}",
            )

        save_col, _ = st.columns([1, 3])
        if save_col.button(
            "💾 Save contact info",
            key=f"save_contact_{deal_id}_{idx}",
            type="primary",
            disabled=not deal_id,
            use_container_width=True,
        ):
            from tools.outreach_queue import update_contact_info
            patch = {
                "linkedin_url": new_linkedin.strip() or None,
                "email": new_email.strip() or None,
                "phone": new_phone.strip() or None,
                "instagram_handle": new_instagram.strip() or None,
            }
            patch = {k: v for k, v in patch.items() if v}
            if not patch:
                st.warning("Nothing to save — all fields are empty.")
            else:
                # ── 1. Local save (queue files + sequence file) ──
                try:
                    n = update_contact_info(rep_id, deal_id, patch)
                except Exception as e:
                    st.error(f"Local save failed: {e}")
                    n = 0

                # ── 2. HubSpot push (fail-soft — local save still counts) ──
                hs_msg: str = ""
                hs_ok = False
                try:
                    from tools.hubspot_client import push_contact_info_to_deal
                    res = push_contact_info_to_deal(deal_id, patch)
                    if res.get("skipped"):
                        hs_msg = f"HubSpot skipped — {res['skipped']}."
                    elif res.get("updated"):
                        hs_ok = True
                        hs_msg = (
                            f"HubSpot updated: {', '.join(res['updated'])}."
                            + (f" Dropped (no such property): {', '.join(res['dropped'])}." if res.get("dropped") else "")
                        )
                    else:
                        hs_msg = "HubSpot returned no updates."
                except Exception as e:
                    hs_msg = f"HubSpot push failed: {e} (local save was OK)."

                if n and hs_ok:
                    st.success(f"Saved locally ({n} file(s)) and pushed to HubSpot. {hs_msg}")
                elif n:
                    st.success(f"Saved locally ({n} file(s)). {hs_msg}")
                elif hs_ok:
                    st.success(f"No local change. {hs_msg}")
                else:
                    st.info(f"Nothing changed locally. {hs_msg}")
                st.rerun()

    st.divider()

    # Message — st.code gives a built-in copy button
    if swapped_to_email and email_subject:
        st.markdown("**Subject** — click the copy icon top-right to copy")
        st.code(email_subject, language=None, wrap_lines=True)
        st.markdown("**Body** — click the copy icon top-right to copy")
    else:
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

def page_inbound():
    """Capture an inbound lead via paste-in text or screenshot upload.

    Flow: input → extract via Claude → editable preview + draft response
    → 'Add to Pipeline' → HubSpot deal at stage='replied' + queued response.
    """
    st.title("📥 Inbound Lead Capture")
    st.caption(
        "Forward an email, paste a WhatsApp/IG message, or drop a screenshot. "
        "I'll extract the lead, draft a response, and put it in the pipeline at "
        "the 'In conversation' stage."
    )

    rep_id = st.session_state.get("active_rep_id", "perry_patraszewski")

    tab_text, tab_image = st.tabs(["📝 Paste text", "🖼  Upload screenshot"])

    extracted_key = "inbound_extracted_lead"  # ExtractedLead dict
    response_key = "inbound_response_draft"    # {subject, body}

    # ── Input tabs ────────────────────────────────────────────────────────────
    with tab_text:
        raw_text = st.text_area(
            "Forwarded message",
            placeholder="Paste the full email, WhatsApp message, IG DM, or SMS here. Headers and quoted threads are fine — I'll clean them.",
            height=240,
            key="inbound_raw_text",
        )
        if st.button("🧠 Extract", type="primary", disabled=not raw_text.strip(), key="extract_text"):
            with st.spinner("Reading the message..."):
                from brain.inbound_extractor import extract_from_text
                lead = extract_from_text(raw_text)
                st.session_state[extracted_key] = lead.to_dict()
                st.session_state.pop(response_key, None)
                st.rerun()

    with tab_image:
        upload = st.file_uploader(
            "Drop a screenshot",
            type=["png", "jpg", "jpeg", "webp", "gif"],
            key="inbound_upload",
        )
        if upload is not None:
            st.image(upload, caption=upload.name, use_container_width=True)
            if st.button("🧠 Extract from image", type="primary", key="extract_image"):
                with st.spinner("Reading the screenshot..."):
                    from brain.inbound_extractor import extract_from_image
                    media_type = upload.type or "image/png"
                    lead = extract_from_image(upload.read(), media_type=media_type)
                    st.session_state[extracted_key] = lead.to_dict()
                    st.session_state.pop(response_key, None)
                    st.rerun()

    # ── Preview / edit / commit ──────────────────────────────────────────────
    lead_dict = st.session_state.get(extracted_key)
    if not lead_dict:
        return

    st.divider()
    st.subheader("📋 Extracted lead — review and edit before committing")

    if lead_dict.get("extraction_error"):
        # Technical failure — DO NOT blame the message.
        st.error(
            f"⚙️ Extraction failed for technical reasons (this is a config issue, "
            f"not a problem with your inbound).\n\n"
            f"**Detail:** {lead_dict['extraction_error']}"
        )
        if "ANTHROPIC_API_KEY" in lead_dict["extraction_error"]:
            st.info(
                "The running Streamlit process can't see your `ANTHROPIC_API_KEY`. "
                "Stop and restart the app from a shell where `.env` has been loaded "
                "(or the key is exported), then try again."
            )
        if st.button("🔁 Try again", key="retry_extract"):
            st.session_state.pop(extracted_key, None)
            st.session_state.pop(response_key, None)
            st.rerun()
        return

    if not lead_dict.get("is_lead"):
        st.error(
            f"The model judged this not to be a real inbound lead.\n\n"
            f"**Reason:** {lead_dict.get('reasoning', 'unspecified')}\n\n"
            "If you disagree (e.g. it's a real lead the model misread), use the "
            "form below to fix the fields and commit anyway."
        )
        # Allow override — fall through to the editable form so Perry can fix
        # anything the model missed and commit despite is_lead=False.
        lead_dict["is_lead"] = True
        st.session_state[extracted_key] = lead_dict
        st.warning("Override active — review every field carefully before committing.")

    confidence = lead_dict.get("confidence") or 0.0
    if confidence < 0.6:
        st.warning(
            f"Low confidence ({confidence:.2f}) — the model wasn't sure about the venue. "
            "Double-check the venue name and intent below."
        )
    else:
        st.success(f"Confidence: {confidence:.2f} · {lead_dict.get('reasoning', '')}")

    # Editable form for the extracted fields.
    with st.form("inbound_edit_form", border=True):
        c1, c2 = st.columns(2)
        with c1:
            venue_name = st.text_input("Venue name *", value=lead_dict.get("venue_name") or "")
            contact_name = st.text_input("Contact name", value=lead_dict.get("contact_name") or "")
            contact_title = st.text_input("Contact title", value=lead_dict.get("contact_title") or "")
            email = st.text_input("Email", value=lead_dict.get("email") or "")
            phone = st.text_input("Phone", value=lead_dict.get("phone") or "")
        with c2:
            website = st.text_input("Website", value=lead_dict.get("website") or "")
            address = st.text_input("Address / city", value=lead_dict.get("address") or "")
            linkedin_url = st.text_input("LinkedIn URL", value=lead_dict.get("linkedin_url") or "")
            instagram_handle = st.text_input(
                "Instagram handle (no @)",
                value=(lead_dict.get("instagram_handle") or "").lstrip("@"),
            )
            venue_type = st.selectbox(
                "Venue type",
                ["restaurant", "hotel", "cafe", "beach_club", "spa",
                 "wellness_center", "gym", "event_company", "other"],
                index=(["restaurant", "hotel", "cafe", "beach_club", "spa",
                        "wellness_center", "gym", "event_company", "other"]
                       .index(lead_dict.get("venue_type") or "other")),
            )
            tier = st.selectbox(
                "Tier (drives revenue estimate)",
                [1, 2, 3],
                index=1,
                help="Tier 1 = €1K/mo · Tier 2 = €750/mo · Tier 3 = €300/mo",
            )

        intent = st.text_input(
            "What they want (intent)",
            value=lead_dict.get("intent") or "",
        )
        inbound_message = st.text_area(
            "The message they sent (will be logged as a HubSpot note)",
            value=lead_dict.get("inbound_message") or "",
            height=150,
        )

        submit_edit = st.form_submit_button("💾 Save edits", use_container_width=True)
        if submit_edit:
            lead_dict.update({
                "venue_name": venue_name, "contact_name": contact_name or None,
                "contact_title": contact_title or None, "email": email or None,
                "phone": phone or None, "website": website or None,
                "address": address or None, "linkedin_url": linkedin_url or None,
                "instagram_handle": instagram_handle or None,
                "venue_type": venue_type, "intent": intent,
                "inbound_message": inbound_message,
            })
            st.session_state[extracted_key] = lead_dict
            st.session_state["inbound_tier"] = tier
            st.session_state.pop(response_key, None)
            st.toast("Edits saved.", icon="💾")
            st.rerun()

    st.session_state.setdefault("inbound_tier", 2)

    # ── Response draft ────────────────────────────────────────────────────────
    st.divider()
    st.subheader("✍️  Response draft")

    if response_key not in st.session_state:
        if st.button("🪄 Generate response draft", type="secondary", key="gen_draft"):
            with st.spinner("Drafting response..."):
                try:
                    from agents.writer import generate_inbound_response
                    draft = generate_inbound_response(
                        venue_name=lead_dict["venue_name"],
                        intent=lead_dict.get("intent") or "",
                        inbound_message=lead_dict.get("inbound_message") or "",
                        contact_name=lead_dict.get("contact_name"),
                        rep_id=rep_id,
                        venue_type=lead_dict.get("venue_type"),
                    )
                    st.session_state[response_key] = draft
                    st.rerun()
                except Exception as e:
                    st.error(f"Draft generation failed: {e}")
    else:
        draft = st.session_state[response_key]
        with st.form("inbound_draft_form", border=True):
            new_subject = st.text_input("Subject", value=draft.get("subject", ""))
            new_body = st.text_area("Body", value=draft.get("body", ""), height=240)
            cols = st.columns([1, 1, 2])
            regenerate = cols[0].form_submit_button("🪄 Regenerate")
            save_draft = cols[1].form_submit_button("💾 Save edits", type="primary")
            if regenerate:
                st.session_state.pop(response_key, None)
                st.rerun()
            if save_draft:
                st.session_state[response_key] = {"subject": new_subject, "body": new_body}
                st.toast("Draft saved.", icon="✏️")

    # ── Commit ───────────────────────────────────────────────────────────────
    st.divider()
    commit_col, discard_col = st.columns([2, 1])

    can_commit = bool(lead_dict.get("venue_name"))
    commit_label = "📨 Add to Pipeline" + (" + queue draft" if response_key in st.session_state else "")
    if commit_col.button(commit_label, type="primary", disabled=not can_commit, use_container_width=True):
        with st.spinner("Onboarding inbound lead..."):
            try:
                from brain.inbound_extractor import ExtractedLead
                from agents.inbound import onboard_inbound

                lead_obj = ExtractedLead(
                    is_lead=True,
                    venue_name=lead_dict["venue_name"],
                    contact_name=lead_dict.get("contact_name"),
                    contact_title=lead_dict.get("contact_title"),
                    email=lead_dict.get("email"),
                    phone=lead_dict.get("phone"),
                    linkedin_url=lead_dict.get("linkedin_url"),
                    instagram_handle=lead_dict.get("instagram_handle"),
                    website=lead_dict.get("website"),
                    address=lead_dict.get("address"),
                    intent=lead_dict.get("intent") or "",
                    inbound_message=lead_dict.get("inbound_message") or "",
                    confidence=float(lead_dict.get("confidence") or 0.5),
                    reasoning=lead_dict.get("reasoning") or "",
                    venue_type=lead_dict.get("venue_type"),
                )
                response_draft = st.session_state.get(response_key) or {}
                result = onboard_inbound(
                    lead_obj,
                    response_draft,
                    rep_id=rep_id,
                    tier=int(st.session_state.get("inbound_tier", 2)),
                )
                if result.get("duplicate_of"):
                    st.success(
                        f"Updated existing deal {result['deal_id']} (was already in HubSpot). "
                        f"Stage set to 'In conversation' and the inbound logged as a note."
                        + (" Response queued for review." if result.get("queue_added") else "")
                    )
                else:
                    st.success(
                        f"Added to pipeline as deal {result['deal_id']} (stage: In conversation)."
                        + (" Response queued — find it on the Daily Queue page." if result.get("queue_added") else "")
                    )
                st.session_state.pop(extracted_key, None)
                st.session_state.pop(response_key, None)
                st.session_state.pop("inbound_raw_text", None)
            except Exception as e:
                st.error(f"Onboard failed: {e}")

    if discard_col.button("🗑  Discard", use_container_width=True):
        st.session_state.pop(extracted_key, None)
        st.session_state.pop(response_key, None)
        st.session_state.pop("inbound_raw_text", None)
        st.rerun()


def main():
    page = sidebar()

    if page == "Pipeline":
        page_pipeline()
    elif page == "Daily Queue":
        page_queue()
    elif page == "Inbound":
        page_inbound()
    elif page == "Run Agent":
        page_run_agent()
    elif page == "Team":
        page_team()
    elif page == "Reports":
        page_reports()


if __name__ == "__main__":
    main()
