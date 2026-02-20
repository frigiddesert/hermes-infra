#!/usr/bin/env bash
# heartbeat.sh — Send VPS status to Cloudflare Worker + execute any queued commands
# Runs every 2 minutes via cron.
# Cron: */2 * * * * /root/scripts/heartbeat.sh >> /var/log/openclaw-heartbeat.log 2>&1

set -euo pipefail

CF_WORKER_URL="PLACEHOLDER_CF_WORKER_URL"   # e.g. https://openclaw-watchdog.YOUR_SUBDOMAIN.workers.dev
WATCHDOG_SECRET="47fcf0304f051a05ba41b9f71f8c712871e99266d2050668"
TELEGRAM_BOT_TOKEN="7990089510:AAHf4U3FLQugxMxZmKoQkP5e04rn8akkPPw"
TELEGRAM_USER_ID="1514810057"
LOG_PREFIX="[heartbeat $(date '+%Y-%m-%d %H:%M:%S')]"

export XDG_RUNTIME_DIR="/run/user/$(id -u)"

# ── Collect service statuses ─────────────────────────────────────────────────
svc_status() {
  local s
  s=$(systemctl is-active "$1" 2>/dev/null) || true
  echo "${s:-inactive}"
}
user_svc_status() {
  local s
  s=$(systemctl --user is-active "$1" 2>/dev/null) || true
  echo "${s:-inactive}"
}

disk_free_gb=$(df / --output=avail -BG | tail -1 | tr -d 'G ')
mem_free_mb=$(awk '/MemAvailable/ {printf "%.0f", $2/1024}' /proc/meminfo)
uptime_str=$(uptime -p | sed 's/up //')

payload=$(cat <<EOF
{
  "hostname": "openclaw-vps",
  "timestamp": $(date +%s),
  "uptime": "$uptime_str",
  "disk_free_gb": $disk_free_gb,
  "mem_free_mb": $mem_free_mb,
  "services": {
    "ollama": "$(svc_status ollama)",
    "postgresql": "$(svc_status postgresql)",
    "openclaw-gateway": "$(user_svc_status openclaw-gateway)",
    "claude-telegram-bot": "$(user_svc_status claude-telegram-bot)",
    "claude-session-relay": "$(user_svc_status claude-session-relay)",
    "claude-inbox-watcher": "$(user_svc_status claude-inbox-watcher)",
    "claude-permission-relay": "$(user_svc_status claude-permission-relay)"
  }
}
EOF
)

# ── Send heartbeat + get commands ────────────────────────────────────────────
response=$(curl -s --max-time 15 -X POST \
  "${CF_WORKER_URL}/heartbeat" \
  -H "Authorization: Bearer ${WATCHDOG_SECRET}" \
  -H "Content-Type: application/json" \
  -d "$payload" 2>&1) || {
  echo "$LOG_PREFIX failed to reach CF Worker"
  exit 0
}

echo "$LOG_PREFIX response: $response"

# ── Execute any pending commands ─────────────────────────────────────────────
commands=$(echo "$response" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    cmds = d.get('commands', [])
    for c in cmds:
        print(c)
except:
    pass
" 2>/dev/null)

send_telegram() {
  local msg="$1"
  curl -s --max-time 10 -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_USER_ID}" \
    -d "text=${msg}" \
    -d "parse_mode=Markdown" > /dev/null 2>&1 || true
}

execute_command() {
  local cmd="$1"
  echo "$LOG_PREFIX executing command: $cmd"
  case "$cmd" in
    restart:all)
      systemctl restart ollama postgresql 2>&1 || true
      systemctl --user restart openclaw-gateway claude-telegram-bot claude-session-relay claude-inbox-watcher claude-permission-relay 2>&1 || true
      send_telegram "✅ *VPS*: restarted all services"
      ;;
    restart:gateway)
      systemctl --user restart openclaw-gateway 2>&1 || true
      send_telegram "✅ *VPS*: restarted \`openclaw-gateway\`"
      ;;
    restart:ollama)
      systemctl restart ollama 2>&1 || true
      send_telegram "✅ *VPS*: restarted \`ollama\`"
      ;;
    restart:postgres)
      systemctl restart postgresql 2>&1 || true
      send_telegram "✅ *VPS*: restarted \`postgresql\`"
      ;;
    restart:telegram-router)
      systemctl --user restart claude-telegram-bot claude-session-relay claude-inbox-watcher claude-permission-relay 2>&1 || true
      send_telegram "✅ *VPS*: restarted telegram-router services"
      ;;
    status)
      # Re-collect and send status
      disk=$(df / --output=avail -BG | tail -1 | tr -d 'G ')
      mem=$(awk '/MemAvailable/ {printf "%.0f", $2/1024}' /proc/meminfo)
      up=$(uptime -p | sed 's/up //')
      gw=$(user_svc_status openclaw-gateway)
      ol=$(svc_status ollama)
      pg=$(svc_status postgresql)
      tb=$(user_svc_status claude-telegram-bot)
      status_icon() { [ "$1" = "active" ] && echo "✅" || echo "❌"; }
      msg="*VPS Status Report*
$(status_icon $gw) \`openclaw-gateway\` — $gw
$(status_icon $ol) \`ollama\` — $ol
$(status_icon $pg) \`postgresql\` — $pg
$(status_icon $tb) \`telegram-bot\` — $tb
💾 Disk: ${disk}GB free | 🧠 RAM: ${mem}MB free
⏱ Uptime: $up"
      send_telegram "$msg"
      ;;
    reboot)
      send_telegram "⚠️ *VPS*: rebooting in 10 seconds..."
      sleep 10
      reboot
      ;;
    *)
      echo "$LOG_PREFIX unknown command: $cmd"
      ;;
  esac
}

if [ -n "$commands" ]; then
  while IFS= read -r cmd; do
    [ -n "$cmd" ] && execute_command "$cmd"
  done <<< "$commands"
fi

echo "$LOG_PREFIX done"
