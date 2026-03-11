#!/usr/bin/env bash
# health-check.sh — Disk and memory alerts only
# Service health is handled by watchdog.sh (every 2 min)
# Runs every 5 min via cron. Zero LLM tokens.
# Sends alerts to Discord (general channel).

DISCORD_TOKEN=$(python3 -c "import json; print(json.load(open('/root/.openclaw/openclaw.json'))['channels']['discord']['token'])" 2>/dev/null || echo "")
DISCORD_CHANNEL_ID="1477761850748834008"  # general
STATE_DIR=/tmp/openclaw-health
mkdir -p $STATE_DIR

send_discord() {
    curl -s -X POST "https://discord.com/api/v10/channels/${DISCORD_CHANNEL_ID}/messages" \
        -H "Authorization: Bot ${DISCORD_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{\"content\": \"$1\"}" > /dev/null
}

alert_once() {
    local key="$1" msg="$2"
    local flag="$STATE_DIR/$key"
    [ ! -f "$flag" ] && touch "$flag" && send_discord "$msg"
}

clear_alert() { rm -f "$STATE_DIR/$1"; }

# Disk check (alert if > 85% used)
DISK_USED=$(df / | awk 'NR==2 {print $5}' | tr -d '%')
if [ "$DISK_USED" -gt 85 ]; then
    alert_once "disk_high" "⚠️ **VPS disk ${DISK_USED}% full** — clean up needed"
else
    clear_alert "disk_high"
fi

# Memory check (alert if < 300MB free)
MEM_FREE=$(free -m | awk '/^Mem:/ {print $7}')
if [ "$MEM_FREE" -lt 300 ]; then
    alert_once "mem_low" "⚠️ **VPS low memory:** only ${MEM_FREE}MB free"
else
    clear_alert "mem_low"
fi
