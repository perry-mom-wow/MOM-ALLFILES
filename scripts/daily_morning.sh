#!/bin/bash
# Morning: discover prospects + email each rep their daily queue
PROJECT_DIR="/Users/perry/Hackaton Weekend with Nick/juice-sales-agent"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"
{
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "☀️  Morning briefing — $(date)"
  echo "═══════════════════════════════════════════════════════════"
  /usr/bin/env python3 -u main.py morning
  echo ""
} >> "$LOG_DIR/morning.log" 2>&1
