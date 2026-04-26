#!/bin/bash
# One-time installer: adds a weekly cron entry that cleans the CRM every Sunday at 23:00.
set -e

PROJECT_DIR="/Users/perry/Hackaton Weekend with Nick/juice-sales-agent"
WRAPPER="$PROJECT_DIR/scripts/weekly_cleanup.sh"

# Make the wrapper executable
chmod +x "$WRAPPER"

# Build the cron line: every Sunday at 23:00 (11pm)
CRON_LINE="0 23 * * 0 \"$WRAPPER\""
TAG="# mom-wow weekly CRM cleanup"

# Get existing crontab (or empty if none), strip any previous mom-wow entry, then add fresh
( crontab -l 2>/dev/null | grep -v "mom-wow weekly CRM cleanup" | grep -v "weekly_cleanup.sh" ; \
  echo "$TAG" ; \
  echo "$CRON_LINE" ) | crontab -

echo "✅ Installed weekly cleanup cron job."
echo ""
echo "Schedule:  Every Sunday at 23:00"
echo "Wrapper:   $WRAPPER"
echo "Logs:      $PROJECT_DIR/logs/cleanup.log"
echo ""
echo "View installed crons:  crontab -l"
echo "Remove this cron:      crontab -l | grep -v weekly_cleanup.sh | crontab -"
