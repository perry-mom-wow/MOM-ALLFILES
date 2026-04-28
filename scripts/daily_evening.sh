#!/bin/bash
# Evening: send Perry an end-of-day summary
PROJECT_DIR="/Users/perry/Hackaton Weekend with Nick/juice-sales-agent"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"
{
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "🌙 Evening summary — $(date)"
  echo "═══════════════════════════════════════════════════════════"
  /usr/bin/env python3 -u main.py evening
  echo ""
} >> "$LOG_DIR/evening.log" 2>&1
