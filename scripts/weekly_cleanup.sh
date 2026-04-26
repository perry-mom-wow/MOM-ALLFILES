#!/bin/bash
# Weekly CRM cleanup — runs `python3 main.py cleanup --apply` and logs to logs/cleanup.log
set -e

PROJECT_DIR="/Users/perry/Hackaton Weekend with Nick/juice-sales-agent"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/cleanup.log"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

{
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "🧹 Weekly CRM cleanup — $(date)"
  echo "═══════════════════════════════════════════════════════════"
  /usr/bin/env python3 -u main.py cleanup --apply
  echo ""
} >> "$LOG_FILE" 2>&1
