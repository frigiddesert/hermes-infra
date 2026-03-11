#!/usr/bin/env bash
# sync-to-vps.sh — Push workspace files to VPS and commit changes to git
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VPS_HOST="openclaw"
VPS_WORKSPACE="/root/.openclaw/workspace"

echo "=== Syncing workspace files to VPS ==="
rsync -av --exclude='memory/' \
  "$REPO_DIR/vps/workspace/" \
  "$VPS_HOST:$VPS_WORKSPACE/"

echo ""
echo "=== Syncing cron scripts to VPS ==="
rsync -av --chmod=+x \
  "$REPO_DIR/vps/cron-scripts/" \
  "$VPS_HOST:/root/.openclaw/cron-scripts/"

echo ""
echo "=== Pulling latest from VPS ==="
rsync -av \
  "$VPS_HOST:$VPS_WORKSPACE/MEMORY.md" \
  "$REPO_DIR/vps/workspace/MEMORY.md" 2>/dev/null || true

rsync -av \
  "$VPS_HOST:$VPS_WORKSPACE/PROGRESS-LOG.md" \
  "$REPO_DIR/vps/workspace/PROGRESS-LOG.md" 2>/dev/null || true

rsync -av \
  "$VPS_HOST:$VPS_WORKSPACE/TODO.md" \
  "$REPO_DIR/vps/workspace/TODO.md" 2>/dev/null || true

echo ""
echo "=== Committing changes ==="
cd "$REPO_DIR"
git add -A
if git diff --cached --quiet; then
  echo "Nothing changed."
else
  git commit -m "sync: workspace files $(date '+%Y-%m-%d %H:%M')"
  git push
  echo "Pushed to GitHub."
fi
