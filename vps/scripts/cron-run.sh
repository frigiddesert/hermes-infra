#!/usr/bin/env bash
# cron-run.sh — Wrapper that runs a command and Telegrams on failure
#
# Usage in crontab:
#   */5 * * * * /root/scripts/cron-run.sh health-watchdog /root/scripts/health-watchdog.sh
#   0 9 */2 * * /root/scripts/cron-run.sh reddit-scanner /root/scripts/reddit-scanner.sh
#
# Args: <job-name> <command> [args...]
# On success: logs to /var/log/openclaw-cron.log, silent
# On failure: logs + sends Telegram alert with last 20 lines of output

set -euo pipefail

JOB_NAME="${1:?Usage: $0 <job-name> <command> [args...]}"
shift
CMD=("$@")

TELEGRAM_BOT_TOKEN="7990089510:AAHf4U3FLQugxMxZmKoQkP5e04rn8akkPPw"
TELEGRAM_USER_ID="1514810057"
LOG_FILE="/var/log/openclaw-cron.log"
TMPOUT=$(mktemp)

send_telegram() {
  curl -s --max-time 10 -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_USER_ID}" \
    --data-urlencode "text=$1" \
    -d "parse_mode=Markdown" > /dev/null 2>&1 || true
}

START=$(date +%s)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] START $JOB_NAME: ${CMD[*]}" >> "$LOG_FILE"

set +e
"${CMD[@]}" > "$TMPOUT" 2>&1
EXIT_CODE=$?
set -e

END=$(date +%s)
ELAPSED=$((END - START))

if [ $EXIT_CODE -eq 0 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] OK $JOB_NAME (${ELAPSED}s)" >> "$LOG_FILE"
  rm -f "$TMPOUT"
  exit 0
fi

# Failed — log and alert
OUTPUT=$(tail -20 "$TMPOUT")
echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAIL $JOB_NAME exit=$EXIT_CODE (${ELAPSED}s)" >> "$LOG_FILE"
echo "$OUTPUT" >> "$LOG_FILE"
rm -f "$TMPOUT"

send_telegram "🔴 *Cron job failed*: \`$JOB_NAME\`
Exit code: $EXIT_CODE | Duration: ${ELAPSED}s

\`\`\`
$(echo "$OUTPUT" | tail -10)
\`\`\`"

exit $EXIT_CODE
