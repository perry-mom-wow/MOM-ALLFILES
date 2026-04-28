#!/bin/bash
# Friday weekly report (cleanup runs first inside send_friday_report)
PROJECT_DIR="/Users/perry/Hackaton Weekend with Nick/juice-sales-agent"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"
{
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "📊 Friday weekly report — $(date)"
  echo "═══════════════════════════════════════════════════════════"
  /usr/bin/env python3 -u main.py report --send
  echo ""
} >> "$LOG_DIR/friday_report.log" 2>&1
