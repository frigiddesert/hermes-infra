#!/usr/bin/env bash
# memory-summarizer.sh — Weekly compression of daily memory logs into MEMORY.md
# Cron: 0 4 * * 0 /root/scripts/cron-run.sh memory-summarizer /root/scripts/memory-summarizer.sh
# (Every Sunday at 4 AM UTC = 7 AM Jeddah — runs BEFORE the Sunday check-in reminder at 5 AM UTC)
#
# Strategy:
# - Daily logs older than 7 days get summarized using openclaw gateway (1 API call/week)
# - Summary is appended to a "Weekly summaries" section in MEMORY.md
# - Old daily files are archived to ~/.openclaw/workspace/memory/archive/
# - If openclaw gateway is unavailable, falls back to simple concatenation

set -euo pipefail

WORKSPACE="/root/.openclaw/workspace"
MEMORY_DIR="$WORKSPACE/memory"
MEMORY_FILE="$WORKSPACE/MEMORY.md"
ARCHIVE_DIR="$MEMORY_DIR/archive"
GATEWAY_TOKEN=$(grep '"token"' /root/.openclaw/openclaw.json 2>/dev/null | tail -1 | tr -d ' ",' | cut -d: -f2 || echo "")
CUTOFF_DAYS=7
LOG_PREFIX="[memory-summarizer $(date '+%Y-%m-%d %H:%M:%S')]"

mkdir -p "$ARCHIVE_DIR"

echo "$LOG_PREFIX Starting memory compression..."

# Find daily log files older than CUTOFF_DAYS
old_files=()
while IFS= read -r f; do
  old_files+=("$f")
done < <(find "$MEMORY_DIR" -maxdepth 1 -name "????-??-??.md" -mtime +"$CUTOFF_DAYS" 2>/dev/null | sort)

if [ ${#old_files[@]} -eq 0 ]; then
  echo "$LOG_PREFIX No files older than ${CUTOFF_DAYS} days to compress"
  exit 0
fi

echo "$LOG_PREFIX Found ${#old_files[@]} files to compress"

# Concatenate old logs
combined=""
file_dates=""
for f in "${old_files[@]}"; do
  fname=$(basename "$f" .md)
  combined+="=== $fname ===\n"
  combined+=$(cat "$f")
  combined+="\n\n"
  file_dates+="$fname, "
done

# Try to summarize via openclaw gateway (cheap 1-call/week)
summary=""
if [ -n "$GATEWAY_TOKEN" ] && command -v openclaw &>/dev/null; then
  echo "$LOG_PREFIX Requesting summary from openclaw gateway..."
  prompt="Summarize the following daily memory logs from an AI assistant into 10-15 bullet points of the most important facts, decisions, and patterns learned. Be concise. Focus on persistent facts about Eric, his preferences, his infrastructure, and important outcomes.\n\n$combined"
  summary=$(echo "$prompt" | timeout 60 openclaw gateway chat \
    --token "$GATEWAY_TOKEN" \
    --model "openrouter/moonshotai/kimi-k2.5" \
    2>/dev/null) || summary=""
fi

# Fallback: extract non-empty lines as bullet points
if [ -z "$summary" ]; then
  echo "$LOG_PREFIX Gateway unavailable, using simple extraction"
  summary=$(echo -e "$combined" | grep -v "^===" | grep -v "^$" | head -30 | sed 's/^/- /')
fi

# Append to MEMORY.md
week_label=$(date '+%Y-W%V')
{
  echo ""
  echo "## Weekly Summary — $week_label (compressed $(echo "${file_dates%,}" | wc -w) days)"
  echo ""
  echo "$summary"
  echo ""
} >> "$MEMORY_FILE"

# Archive old files
for f in "${old_files[@]}"; do
  mv "$f" "$ARCHIVE_DIR/"
  echo "$LOG_PREFIX Archived: $(basename "$f")"
done

echo "$LOG_PREFIX Done. Compressed ${#old_files[@]} files into MEMORY.md."
