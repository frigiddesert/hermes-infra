#!/usr/bin/env bash
# free-model-digest.sh — Daily check for new free OpenRouter models
# Cron: 0 7 * * * /root/scripts/free-model-digest.sh >> /var/log/openclaw-models.log 2>&1
# (Every day at 7 AM UTC = 10 AM Jeddah)

set -euo pipefail

TELEGRAM_BOT_TOKEN="7990089510:AAHf4U3FLQugxMxZmKoQkP5e04rn8akkPPw"
TELEGRAM_USER_ID="1514810057"
OPENROUTER_API_KEY="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"
STATE_FILE="/root/.openclaw/free-models-known.txt"
LOG_PREFIX="[free-models $(date '+%Y-%m-%d %H:%M:%S')]"

send_telegram() {
  curl -s --max-time 15 -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_USER_ID}" \
    --data-urlencode "text=$1" \
    -d "parse_mode=Markdown" > /dev/null 2>&1 || true
}

echo "$LOG_PREFIX Checking OpenRouter free models..."

TMPJSON=$(mktemp)
trap 'rm -f "$TMPJSON"' EXIT

curl -s --max-time 20 \
  -H "Authorization: Bearer ${OPENROUTER_API_KEY}" \
  -H "User-Agent: openclaw-vps-bot/1.0" \
  "https://openrouter.ai/api/v1/models" \
  -o "$TMPJSON" 2>&1 || {
  echo "$LOG_PREFIX Failed to reach OpenRouter"
  exit 0
}

# Extract free model IDs sorted
current_ids=$(python3 - "$TMPJSON" << 'PYEOF'
import sys, json
with open(sys.argv[1]) as f:
    data = json.load(f)
free = sorted(m['id'] for m in data.get('data',[]) if m.get('id','').endswith(':free'))
print('\n'.join(free))
PYEOF
) || {
  echo "$LOG_PREFIX Parse failed"
  exit 0
}

if [ -z "$current_ids" ]; then
  echo "$LOG_PREFIX No free models returned"
  exit 0
fi

total=$(echo "$current_ids" | wc -l)
echo "$LOG_PREFIX Found $total free models"

# First run — just save the list, no alert
if [ ! -f "$STATE_FILE" ]; then
  echo "$current_ids" > "$STATE_FILE"
  echo "$LOG_PREFIX First run — saved baseline ($total models)"
  send_telegram "🆓 *Free model baseline saved*: $total free models on OpenRouter.\nI'll alert you when new ones appear."
  exit 0
fi

# Compare with known list
new_models=$(comm -23 <(echo "$current_ids" | sort) <(sort "$STATE_FILE"))
gone_models=$(comm -13 <(echo "$current_ids" | sort) <(sort "$STATE_FILE"))

if [ -z "$new_models" ] && [ -z "$gone_models" ]; then
  echo "$LOG_PREFIX No changes ($total free models)"
  exit 0
fi

# Build alert message
msg="🆕 *OpenRouter Free Model Changes*\n"

if [ -n "$new_models" ]; then
  msg+="*New free models:*\n"
  while IFS= read -r m; do
    msg+="• \`$m\`\n"
  done <<< "$new_models"
fi

if [ -n "$gone_models" ]; then
  msg+="\n*Removed:*\n"
  while IFS= read -r m; do
    msg+="• ~~\`$m\`~~\n"
  done <<< "$gone_models"
fi

msg+="\nTotal free: $total  |  Use /models-free to switch"

echo "$LOG_PREFIX Sending alert: new=$(echo "$new_models" | grep -c . || true) gone=$(echo "$gone_models" | grep -c . || true)"
send_telegram "$msg"

# Update state
echo "$current_ids" > "$STATE_FILE"
echo "$LOG_PREFIX State updated"
