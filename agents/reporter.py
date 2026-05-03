"""Reporter agent: generate pipeline reports with Plotly charts and send Friday emails."""
from __future__ import annotations

import io
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

from config.settings import load_icp, REPORT_RECIPIENTS
from config.brand import FUNNEL_COLOURS, CHART_SEQUENCE, GREEN, GREEN_DARK, CREAM, TERRACOTTA, ORANGE, MUSTARD, WHITE, BLACK
from tools import hubspot_client as hs
from tools.email_sender import send_report_email

ICP = load_icp()
STAGE_ORDER = [s["id"] for s in ICP["pipeline_stages"]]
STAGE_LABELS = {s["id"]: s["label"] for s in ICP["pipeline_stages"]}
TIER_REVENUE = {
    1: ICP["tiers"]["tier_1"]["monthly_revenue_eur"],
    2: ICP["tiers"]["tier_2"]["monthly_revenue_eur"],
    3: ICP["tiers"]["tier_3"]["monthly_revenue_eur"],
}


# Inverse of HS_STAGE_MAP — translate HubSpot's stage IDs back to ours
_HS_TO_OURS = {
    "appointmentscheduled":  "prospect",        # Spotted
    "qualifiedtobuy":        "contacted",       # Outreach sent
    "presentationscheduled": "replied",         # In conversation
    "decisionmakerboughtin": "tasting_booked",  # Tasting booked
    "contractsent":          "tasting_done",    # Tasting done
    "closedwon":             "won",             # Stocked
    "closedlost":            "lost",            # Passed
}


def _deals_to_df(deals: list[dict]) -> pd.DataFrame:
    rows = []
    for d in deals:
        props = d.get("properties", {})
        hs_stage = (props.get("dealstage") or "").lower()
        # Map HubSpot's stage ID back to our internal one (else keep as-is)
        stage = _HS_TO_OURS.get(hs_stage, hs_stage or "prospect")
        rows.append({
            "deal_id": d.get("id"),
            "name": props.get("dealname", ""),
            "stage": stage,
            "amount": float(props.get("amount") or 0),
            "owner": props.get("hubspot_owner_id", "unknown"),
            "created": props.get("createdate", ""),
            "modified": props.get("hs_lastmodifieddate", ""),
        })
    return pd.DataFrame(rows)


def _make_funnel_chart(df: pd.DataFrame) -> Path:
    stage_counts = []
    for stage in STAGE_ORDER:
        count = len(df[df["stage"] == stage])
        stage_counts.append({"stage": STAGE_LABELS[stage], "count": count})

    labels = [s["stage"] for s in stage_counts]
    values = [s["count"] for s in stage_counts]

    fig = go.Figure(go.Funnel(
        y=labels,
        x=values,
        textinfo="value+percent initial",
        marker={"color": FUNNEL_COLOURS[:len(labels)]},
        textfont={"color": WHITE},
    ))
    fig.update_layout(
        title={"text": "Pipeline Funnel", "font": {"color": GREEN_DARK, "size": 16}},
        font={"family": "Arial", "size": 13, "color": BLACK},
        plot_bgcolor=WHITE,
        paper_bgcolor=CREAM,
    )
    return _save_chart(fig, "funnel")


def _make_tier_bar(df: pd.DataFrame) -> Path:
    # Count and value by tier — approximate from amount field
    tier_buckets = {
        "Tier 1 (€1K+)": df[df["amount"] >= 1000],
        "Tier 2 (€500-1K)": df[(df["amount"] >= 500) & (df["amount"] < 1000)],
        "Tier 3 (<€500)": df[df["amount"] < 500],
    }
    labels = list(tier_buckets.keys())
    counts = [len(v) for v in tier_buckets.values()]
    values = [v["amount"].sum() for v in tier_buckets.values()]

    fig = go.Figure()
    fig.add_trace(go.Bar(name="# Deals", x=labels, y=counts, yaxis="y", marker_color=GREEN))
    fig.add_trace(go.Bar(name="€ Pipeline", x=labels, y=values, yaxis="y2", marker_color=MUSTARD))
    fig.update_layout(
        title={"text": "Pipeline by Tier", "font": {"color": GREEN_DARK, "size": 16}},
        barmode="group",
        yaxis={"title": "# Deals", "color": BLACK},
        yaxis2={"title": "€ Pipeline Value", "overlaying": "y", "side": "right", "color": BLACK},
        plot_bgcolor=WHITE,
        paper_bgcolor=CREAM,
        legend={"orientation": "h"},
        font={"family": "Arial", "color": BLACK},
    )
    return _save_chart(fig, "tier_bar")


def _make_weekly_activity(df: pd.DataFrame) -> Path:
    # Deals modified in each of the last 8 weeks
    weeks = []
    counts = []
    for i in range(7, -1, -1):
        week_end = date.today() - timedelta(weeks=i)
        week_start = week_end - timedelta(weeks=1)
        label = week_start.strftime("%d %b")
        if df.empty or "modified" not in df.columns:
            counts.append(0)
        else:
            mask = df["modified"].apply(
                lambda x: week_start.isoformat() <= (x or "")[:10] < week_end.isoformat()
            )
            counts.append(mask.sum())
        weeks.append(label)

    fig = go.Figure(go.Bar(
        x=weeks, y=counts,
        marker_color=GREEN,
        marker_line_color=GREEN_DARK,
        marker_line_width=1.5,
        text=counts,
        textposition="outside",
        textfont={"color": BLACK},
    ))
    fig.update_layout(
        title={"text": "Weekly Pipeline Activity (Deals Updated)", "font": {"color": GREEN_DARK, "size": 16}},
        xaxis_title="Week",
        yaxis_title="Deals",
        plot_bgcolor=WHITE,
        paper_bgcolor=CREAM,
        font={"family": "Arial", "color": BLACK},
    )
    return _save_chart(fig, "weekly_activity")


def _make_stage_donut(df: pd.DataFrame) -> Path:
    stage_counts = df.groupby("stage").size().reset_index(name="count")
    stage_counts["label"] = stage_counts["stage"].map(STAGE_LABELS)

    fig = px.pie(
        stage_counts,
        names="label",
        values="count",
        hole=0.45,
        title="Deals by Stage",
        color_discrete_sequence=CHART_SEQUENCE,
    )
    fig.update_layout(
        title={"font": {"color": GREEN_DARK, "size": 16}},
        plot_bgcolor=WHITE,
        paper_bgcolor=CREAM,
        font={"family": "Arial", "color": BLACK},
        legend={"font": {"color": BLACK}},
    )
    return _save_chart(fig, "stage_donut")


def _save_chart(fig: go.Figure, name: str) -> Path:
    out = Path(tempfile.mkdtemp()) / f"{name}.png"
    fig.write_image(str(out), width=700, height=400, scale=2)
    return out


def generate_report(deals: Optional[List[dict]] = None) -> dict:
    """Build full pipeline report. Returns dict with summary text and chart paths."""
    deals = deals or hs.get_all_deals()
    df = _deals_to_df(deals)

    total = len(df)
    won = len(df[df["stage"] == "won"])
    lost = len(df[df["stage"] == "lost"])
    active = total - won - lost
    pipeline_value = df[df["stage"].isin(["prospect", "contacted", "replied", "tasting_booked", "nurture"])]["amount"].sum()
    nurture_count = len(df[df["stage"] == "nurture"])

    # Top 3 prospects (highest value, not won/lost)
    top3 = (
        df[~df["stage"].isin(["won", "lost"])]
        .sort_values("amount", ascending=False)
        .head(3)
    )
    top3_list = [
        f"{row['name']} — {STAGE_LABELS.get(row['stage'], row['stage'])} — €{row['amount']:.0f}/mo"
        for _, row in top3.iterrows()
    ]

    charts = []
    if total > 0:
        charts.append(_make_funnel_chart(df))
        charts.append(_make_tier_bar(df))
        charts.append(_make_weekly_activity(df))
        charts.append(_make_stage_donut(df))

    return {
        "total_deals": total,
        "active_deals": active,
        "won": won,
        "lost": lost,
        "nurture_count": nurture_count,
        "pipeline_value_eur": pipeline_value,
        "top3_prospects": top3_list,
        "chart_paths": charts,
        "generated_at": datetime.utcnow().isoformat(),
    }


def _build_email_html(report: dict) -> str:
    top3_html = "".join(f"<li style='margin-bottom:6px'>{p}</li>" for p in report["top3_prospects"]) or "<li>None yet</li>"
    return f"""
<html>
<body style="font-family:Arial,sans-serif;color:{BLACK};max-width:680px;margin:auto;background:{WHITE}">

  <!-- Header -->
  <div style="background:{GREEN_DARK};padding:28px 32px;border-radius:8px 8px 0 0">
    <h1 style="margin:0;color:{WHITE};font-size:22px;font-weight:700;letter-spacing:-0.5px">
      MOM · Longevity Alchemists Sales Report
    </h1>
    <p style="margin:6px 0 0;color:{WHITE}cc;font-size:14px">
      {date.today().strftime('%d %B %Y')}
    </p>
  </div>

  <!-- KPI row -->
  <div style="background:{CREAM};padding:24px 32px;display:flex;gap:12px">
    <table style="border-collapse:collapse;width:100%">
      <tr>
        <td style="padding:14px 16px;background:{WHITE};border:1px solid {GREEN}33;border-radius:4px;width:25%">
          <div style="font-size:11px;color:{BLACK}88;text-transform:uppercase;letter-spacing:0.5px">Total Deals</div>
          <div style="font-size:28px;font-weight:700;color:{GREEN_DARK}">{report['total_deals']}</div>
        </td>
        <td style="width:12px"></td>
        <td style="padding:14px 16px;background:{WHITE};border:1px solid {GREEN}33;border-radius:4px;width:25%">
          <div style="font-size:11px;color:{BLACK}88;text-transform:uppercase;letter-spacing:0.5px">Pipeline Value</div>
          <div style="font-size:28px;font-weight:700;color:{GREEN}">€{report['pipeline_value_eur']:,.0f}<span style="font-size:14px;font-weight:400">/mo</span></div>
        </td>
        <td style="width:12px"></td>
        <td style="padding:14px 16px;background:{WHITE};border:1px solid {GREEN}33;border-radius:4px;width:25%">
          <div style="font-size:11px;color:{BLACK}88;text-transform:uppercase;letter-spacing:0.5px">Won</div>
          <div style="font-size:28px;font-weight:700;color:{GREEN_DARK}">{report['won']}</div>
        </td>
        <td style="width:12px"></td>
        <td style="padding:14px 16px;background:{WHITE};border:1px solid {GREEN}33;border-radius:4px;width:25%">
          <div style="font-size:11px;color:{BLACK}88;text-transform:uppercase;letter-spacing:0.5px">Nurture</div>
          <div style="font-size:28px;font-weight:700;color:{MUSTARD}">{report['nurture_count']}</div>
        </td>
      </tr>
    </table>
  </div>

  <!-- Top 3 -->
  <div style="padding:24px 32px;background:{WHITE}">
    <h3 style="margin:0 0 12px;color:{GREEN_DARK};font-size:15px;text-transform:uppercase;letter-spacing:0.5px">
      Top 3 Prospects to Prioritise
    </h3>
    <ul style="margin:0;padding-left:18px;line-height:2;color:{BLACK}">
      {top3_html}
    </ul>
  </div>

  <!-- Charts note -->
  <div style="padding:16px 32px;background:{CREAM};border-top:2px solid {GREEN}22">
    <p style="margin:0;font-size:13px;color:{BLACK}88">
      Charts attached — pipeline funnel, tier breakdown, weekly activity, stage split.
    </p>
  </div>

  <!-- Footer -->
  <div style="background:{GREEN_DARK};padding:16px 32px;border-radius:0 0 8px 8px">
    <p style="margin:0;font-size:11px;color:{WHITE}88">
      Generated by the MOM Sales Agent · {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC
    </p>
  </div>

</body></html>
"""


def send_friday_report(skip_cleanup: bool = False) -> None:
    """Run weekly cleanup, generate report, email it."""
    if not skip_cleanup:
        try:
            from agents.cleanup import cleanup
            print("🧹 Weekly CRM hygiene pass...")
            cleanup(dry_run=False)
        except Exception as e:
            print(f"⚠️  Cleanup failed (continuing with report): {e}")

    report = generate_report()
    html = _build_email_html(report)
    send_report_email(
        to_emails=REPORT_RECIPIENTS,
        subject=f"🍋 MOM · Weekly Sales Report · {date.today().strftime('%d %B %Y')}",
        html_body=html,
        attachments=report["chart_paths"],
    )
    print(f"✅ Friday report sent to {', '.join(REPORT_RECIPIENTS)}")
