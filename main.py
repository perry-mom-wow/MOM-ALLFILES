"""
MOM Sales Agent (Longevity Alchemists) — CLI entry point.

Usage:
  python main.py discover --location "Lisboa" --types beach_club restaurant --rep marcus
  python main.py followup
  python main.py report [--send]
  python main.py dashboard
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Optional

from config.settings import load_reps, load_icp

ICP = load_icp()
VENUE_TYPES = ICP["venue_types"]


def _pick_rep(rep_id: Optional[str]) -> str:
    """Interactively pick a rep if not supplied."""
    reps = load_reps()
    if not reps:
        print("❌ No reps configured. Add a rep in the dashboard (Team page) or edit config/reps.yaml.")
        sys.exit(1)

    if rep_id:
        match = next((r for r in reps if r["id"] == rep_id), None)
        if match:
            return rep_id
        print(f"⚠️  Rep '{rep_id}' not found. Please choose from the list below.")

    print("\n👋 Who are we plugging into today?\n")
    for i, r in enumerate(reps, 1):
        print(f"  [{i}] {r['name']} — {r['title']}")
    print(f"  [{len(reps)+1}] Add a new rep (opens dashboard)")
    print()

    while True:
        choice = input("Enter number: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(reps):
                chosen = reps[idx]
                print(f"\n✅ Plugging into {chosen['name']}. All messages will be in their voice.\n")
                return chosen["id"]
            if int(choice) == len(reps) + 1:
                print("Opening dashboard... run: streamlit run dashboard/app.py")
                sys.exit(0)
        print("Invalid choice. Try again.")


def cmd_discover(args):
    rep_id = _pick_rep(args.rep)
    location = args.location
    venue_types = args.types or ["beach_club", "restaurant", "hotel"]

    print(f"\n🔍 Discovering prospects in {location!r}...")
    print(f"   Venue types: {', '.join(venue_types)}")
    print(f"   Max per type: {args.max_per}\n")

    from agents.discovery import discover_prospects
    prospects = discover_prospects(location, venue_types, max_per_type=args.max_per)
    print(f"✅ Found {len(prospects)} prospects.\n")

    for i, raw in enumerate(prospects, 1):
        print(f"[{i}/{len(prospects)}] Researching {raw.name}...")
        try:
            from agents.researcher import research_prospect
            profile = research_prospect(raw)

            print(f"    ✓ Profile: {profile.description[:80]}...")
            print(f"    ✓ Hook: {profile.personalisation_hook[:80]}...")
            print(f"    ✓ Tier {profile.tier}")

            print(f"    ✓ Generating outreach sequence...")
            from agents.writer import generate_sequence
            sequence = generate_sequence(profile, rep_id)

            print(f"    ✓ Onboarding to HubSpot...")
            from agents.crm import onboard_prospect, GatekeeperRejection, DuplicateInCRM
            try:
                result = onboard_prospect(profile, sequence, rep_id)
                print(f"    ✅ Done — Deal ID {result['deal_id']} | €{result['revenue_potential_eur']}/mo | Next follow-up: {result['next_followup']}\n")
            except DuplicateInCRM as dup:
                print(f"    🔄 {dup}\n")
            except GatekeeperRejection as gk:
                print(f"    🚫 {gk}\n")

        except Exception as e:
            print(f"    ❌ Error: {e}\n")

    reps = load_reps()
    rep_name = next((r["name"] for r in reps if r["id"] == rep_id), rep_id)
    print(f"\n📬 Check {rep_name}'s daily queue: python main.py queue --rep {rep_id}")
    print("   Or open the dashboard: streamlit run dashboard/app.py")


def cmd_followup(args):
    rep_id = _pick_rep(args.rep) if hasattr(args, "rep") else None
    print("\n▶️  Running daily follow-up sequencer...\n")
    from agents.sequencer import run_daily
    result = run_daily(today=date.today())
    print(f"✅ Done.")
    print(f"   Deals checked: {result['deals_checked']}")
    print(f"   Messages queued: {result['messages_queued']}")
    if result["queued"]:
        print(f"\n   Queued messages:")
        for q in result["queued"]:
            print(f"     • {q}")
    if result["skipped"]:
        print(f"\n   Skipped:")
        for s in result["skipped"]:
            print(f"     • {s}")


def cmd_report(args):
    print("\n📊 Generating report...\n")
    from agents.reporter import generate_report, send_friday_report
    report = generate_report()
    print(f"  Total deals:    {report['total_deals']}")
    print(f"  Active:         {report['active_deals']}")
    print(f"  Won:            {report['won']}")
    print(f"  Nurture:        {report['nurture_count']}")
    print(f"  Pipeline value: €{report['pipeline_value_eur']:,.0f}/mo")
    print(f"\n  Top 3 prospects:")
    for p in report["top3_prospects"]:
        print(f"    • {p}")
    print(f"\n  Charts saved to:")
    for p in report["chart_paths"]:
        print(f"    {p}")

    if args.send:
        print("\n📧 Sending email report...")
        send_friday_report()


def cmd_queue(args):
    rep_id = _pick_rep(args.rep)
    from tools.outreach_queue import load_queue, format_queue_for_display
    items = load_queue(rep_id)
    reps = load_reps()
    rep_name = next((r["name"] for r in reps if r["id"] == rep_id), rep_id)
    print(f"\n📬 Daily Queue for {rep_name} — {date.today().isoformat()}\n")
    print(format_queue_for_display(items))


def cmd_cleanup(args):
    from agents.cleanup import cleanup
    cleanup(dry_run=not args.apply)


def cmd_dashboard(_args):
    import subprocess
    import os
    dashboard = str(__file__.replace("main.py", "dashboard/app.py"))
    subprocess.run(["streamlit", "run", dashboard], env={**os.environ})


def main():
    parser = argparse.ArgumentParser(description="MOM AI Sales Agent — Longevity Alchemists")
    sub = parser.add_subparsers(dest="command")

    # discover
    p_discover = sub.add_parser("discover", help="Discover and onboard new prospects")
    p_discover.add_argument("--location", "-l", required=True, help="City or area (e.g. 'Lisboa')")
    p_discover.add_argument("--types", "-t", nargs="+", choices=VENUE_TYPES, help="Venue types to target")
    p_discover.add_argument("--max-per", type=int, default=10, help="Max prospects per venue type")
    p_discover.add_argument("--rep", "-r", help="Rep ID (e.g. marcus, laura)")

    # followup
    p_followup = sub.add_parser("followup", help="Run daily follow-up sequencer")
    p_followup.add_argument("--rep", "-r", help="Rep ID (optional)")

    # report
    p_report = sub.add_parser("report", help="Generate pipeline report")
    p_report.add_argument("--send", action="store_true", help="Also email the report")

    # queue
    p_queue = sub.add_parser("queue", help="View today's outreach queue for a rep")
    p_queue.add_argument("--rep", "-r", help="Rep ID")

    # cleanup
    p_cleanup = sub.add_parser("cleanup", help="Find and remove junk deals from HubSpot")
    p_cleanup.add_argument("--apply", action="store_true", help="Actually delete (default is dry-run)")

    # dashboard
    sub.add_parser("dashboard", help="Open Streamlit dashboard")

    args = parser.parse_args()

    if args.command == "discover":
        cmd_discover(args)
    elif args.command == "followup":
        cmd_followup(args)
    elif args.command == "report":
        cmd_report(args)
    elif args.command == "queue":
        cmd_queue(args)
    elif args.command == "cleanup":
        cmd_cleanup(args)
    elif args.command == "dashboard":
        cmd_dashboard(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
