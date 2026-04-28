#!/bin/bash
# Installs all MOM Sales Agent cron jobs:
#   08:00 Mon-Fri → Morning briefing (discover + email each rep)
#   18:00 Mon-Fri → Evening summary (email Perry what happened today)
#   17:00 Friday  → Weekly Friday report (also runs cleanup first)
#   23:00 Sunday  → Standalone weekly cleanup
set -e

PROJECT_DIR="/Users/perry/Hackaton Weekend with Nick/juice-sales-agent"
SCRIPTS="$PROJECT_DIR/scripts"

# Make wrappers executable
chmod +x "$SCRIPTS/daily_morning.sh" \
         "$SCRIPTS/daily_evening.sh" \
         "$SCRIPTS/friday_report.sh" \
         "$SCRIPTS/weekly_cleanup.sh"

TAG="# mom-wow daily automation"

# Strip ALL old mom-wow / weekly_cleanup entries, then install fresh
( crontab -l 2>/dev/null \
    | grep -v "mom-wow" \
    | grep -v "weekly_cleanup.sh" \
    | grep -v "daily_morning.sh" \
    | grep -v "daily_evening.sh" \
    | grep -v "friday_report.sh" ; \
  echo "$TAG" ; \
  echo "0 8 * * 1-5 \"$SCRIPTS/daily_morning.sh\"" ; \
  echo "0 18 * * 1-5 \"$SCRIPTS/daily_evening.sh\"" ; \
  echo "0 17 * * 5 \"$SCRIPTS/friday_report.sh\"" ; \
  echo "0 23 * * 0 \"$SCRIPTS/weekly_cleanup.sh\"" ; \
) | crontab -

echo "✅ Full automation installed."
echo ""
echo "Schedule:"
echo "  08:00 Mon-Fri → Morning briefing (discover + email Marcus & Laura)"
echo "  18:00 Mon-Fri → Evening summary  (email Perry what happened today)"
echo "  17:00 Friday  → Weekly report    (Friday email with charts)"
echo "  23:00 Sunday  → Weekly cleanup   (junk + dedupe)"
echo ""
echo "Logs:"
echo "  $PROJECT_DIR/logs/morning.log"
echo "  $PROJECT_DIR/logs/evening.log"
echo "  $PROJECT_DIR/logs/friday_report.log"
echo "  $PROJECT_DIR/logs/cleanup.log"
echo ""
echo "View installed crons:  crontab -l"
echo "Remove all jobs:       crontab -l | grep -v 'mom-wow\\|_morning\\|_evening\\|friday_report\\|weekly_cleanup' | crontab -"
