#!/usr/bin/env bash
# health-watchdog.sh — VPS service health monitor + self-healer
# Runs every 5 minutes via cron. Restarts dead services and sends Telegram alerts.
# Cron: */5 * * * * /root/scripts/health-watchdog.sh >> /var/log/openclaw-watchdog.log 2>&1

set -euo pipefail

TELEGRAM_BOT_TOKEN="7990089510:AAHf4U3FLQugxMxZmKoQkP5e04rn8akkPPw"
TELEGRAM_USER_ID="1514810057"
LOCK_FILE="/tmp/health-watchdog.lock"
LOG_PREFIX="[watchdog $(date '+%Y-%m-%d %H:%M:%S')]"

# ── Lock to prevent concurrent runs ──────────────────────────────────────────
if [ -f "$LOCK_FILE" ]; then
  pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "$LOG_PREFIX already running (pid $pid), skipping"
    exit 0
  fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# ── Telegram direct notify (works even if openclaw is down) ──────────────────
send_telegram() {
  local message="$1"
  curl -s --max-time 10 -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_USER_ID}" \
    -d "text=${message}" \
    -d "parse_mode=Markdown" > /dev/null 2>&1 || true
}

# ── Service check + restart ───────────────────────────────────────────────────
declare -A FAILED=()

check_system_service() {
  local svc="$1"
  if ! systemctl is-active --quiet "$svc" 2>/dev/null; then
    echo "$LOG_PREFIX $svc is DOWN — restarting"
    systemctl restart "$svc" 2>&1 || true
    sleep 3
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
      echo "$LOG_PREFIX $svc restarted OK"
      send_telegram "✅ Auto-healed \`$svc\` on VPS"
    else
      echo "$LOG_PREFIX $svc STILL DOWN after restart"
      FAILED["$svc"]="failed"
    fi
  else
    echo "$LOG_PREFIX $svc OK"
  fi
}

check_user_service() {
  local svc="$1"
  if ! systemctl --user is-active --quiet "$svc" 2>/dev/null; then
    echo "$LOG_PREFIX [user] $svc is DOWN — restarting"
    systemctl --user restart "$svc" 2>&1 || true
    sleep 3
    if systemctl --user is-active --quiet "$svc" 2>/dev/null; then
      echo "$LOG_PREFIX [user] $svc restarted OK"
      send_telegram "✅ Auto-healed \`$svc\` on VPS"
    else
      echo "$LOG_PREFIX [user] $svc STILL DOWN after restart"
      FAILED["$svc"]="failed"
    fi
  else
    echo "$LOG_PREFIX [user] $svc OK"
  fi
}

# System services
check_system_service "ollama"
check_system_service "postgresql"

# User services (openclaw gateway + telegram router)
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
check_user_service "openclaw-gateway"
check_user_service "claude-telegram-bot"
check_user_service "claude-session-relay"
check_user_service "claude-inbox-watcher"
check_user_service "claude-permission-relay"

# ── Disk + memory check ───────────────────────────────────────────────────────
disk_free_gb=$(df / --output=avail -BG | tail -1 | tr -d 'G ')
mem_free_mb=$(awk '/MemAvailable/ {printf "%.0f", $2/1024}' /proc/meminfo)

if [ "$disk_free_gb" -lt 2 ]; then
  echo "$LOG_PREFIX DISK LOW: ${disk_free_gb}GB free"
  send_telegram "⚠️ *VPS Disk Low* — only \`${disk_free_gb}GB\` free on openclaw-vps"
fi

if [ "$mem_free_mb" -lt 200 ]; then
  echo "$LOG_PREFIX MEMORY LOW: ${mem_free_mb}MB free"
  send_telegram "⚠️ *VPS Memory Low* — only \`${mem_free_mb}MB\` free on openclaw-vps"
fi

# ── Alert on persistent failures ──────────────────────────────────────────────
if [ ${#FAILED[@]} -gt 0 ]; then
  failed_list=$(printf '• `%s`\n' "${!FAILED[@]}")
  echo "$LOG_PREFIX Sending failure alert to Telegram"
  send_telegram "🔴 *VPS services still DOWN after auto-repair attempt:*\n${failed_list}\n\nSend /restart to watchdog or SSH in."
fi

echo "$LOG_PREFIX done (disk: ${disk_free_gb}GB, mem: ${mem_free_mb}MB)"
