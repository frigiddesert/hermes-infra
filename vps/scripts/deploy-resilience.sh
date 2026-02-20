#!/usr/bin/env bash
# deploy-resilience.sh — Deploy watchdog + heartbeat scripts to VPS and configure cron
# Run from local machine after `wrangler deploy` has succeeded and you have CF_WORKER_URL.
#
# Usage: CF_WORKER_URL=https://openclaw-watchdog.SUBDOMAIN.workers.dev ./scripts/deploy-resilience.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)"
VPS_HOST="openclaw"
CF_WORKER_URL="${CF_WORKER_URL:-PLACEHOLDER_CF_WORKER_URL}"

if [ "$CF_WORKER_URL" = "PLACEHOLDER_CF_WORKER_URL" ]; then
  echo "ERROR: Set CF_WORKER_URL before running."
  echo "  export CF_WORKER_URL=https://openclaw-watchdog.YOUR_SUBDOMAIN.workers.dev"
  exit 1
fi

echo "=== Deploying resilience scripts to VPS ==="

# Inject the real CF Worker URL into heartbeat.sh before uploading
TMP=$(mktemp)
sed "s|PLACEHOLDER_CF_WORKER_URL|${CF_WORKER_URL}|g" \
  "$REPO_DIR/vps/scripts/heartbeat.sh" > "$TMP"

ssh "$VPS_HOST" 'mkdir -p /root/scripts'
scp "$TMP" "$VPS_HOST:/root/scripts/heartbeat.sh"
scp "$REPO_DIR/vps/scripts/health-watchdog.sh" "$VPS_HOST:/root/scripts/health-watchdog.sh"
rm "$TMP"

ssh "$VPS_HOST" 'chmod +x /root/scripts/heartbeat.sh /root/scripts/health-watchdog.sh'

echo "=== Installing cron jobs ==="
# Write cron entries (idempotent — only add if not present)
ssh "$VPS_HOST" bash <<'REMOTESCRIPT'
CRON_HEARTBEAT="*/2 * * * * /root/scripts/heartbeat.sh >> /var/log/openclaw-heartbeat.log 2>&1"
CRON_WATCHDOG="*/5 * * * * /root/scripts/health-watchdog.sh >> /var/log/openclaw-watchdog.log 2>&1"

current=$(crontab -l 2>/dev/null || true)

add_cron() {
  local entry="$1"
  if echo "$current" | grep -qF "$entry"; then
    echo "  already present: $entry"
  else
    (echo "$current"; echo "$entry") | crontab -
    current=$(crontab -l)
    echo "  added: $entry"
  fi
}

add_cron "$CRON_HEARTBEAT"
add_cron "$CRON_WATCHDOG"

echo "Current crontab:"
crontab -l
REMOTESCRIPT

echo ""
echo "=== Verifying scripts ==="
ssh "$VPS_HOST" 'ls -la /root/scripts/'

echo ""
echo "=== Running first heartbeat (dry run check) ==="
ssh "$VPS_HOST" '/root/scripts/heartbeat.sh && echo OK'

echo ""
echo "Done! Resilience scripts are live."
echo "  Heartbeat: every 2 min → ${CF_WORKER_URL}/heartbeat"
echo "  Watchdog:  every 5 min → checks + restarts dead services"
echo ""
echo "Test health endpoint:"
echo "  curl ${CF_WORKER_URL}/health"
