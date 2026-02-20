#!/usr/bin/env bash
# remind.sh — Zero-token Telegram reminder sender
# Usage: /root/scripts/remind.sh "Your message here"
#
# Designed to be called from cron AT ANY TIME (not just the specific minute).
# The AI agent (openclaw) adds cron entries; this script does the actual sending.
#
# Example cron entries added by openclaw:
#   30 18 20 2 * /root/scripts/remind.sh "Time to call your friend! Window: 6:30-7:05 PM Jeddah"
#   45 22 20 2 * /root/scripts/remind.sh "Airport call window: 10:45-11:15 PM Jeddah (2:45 PM EST)"
#
# Cron timezone: VPS is UTC. Jeddah = UTC+3. New York EST = UTC-5.
# So Jeddah 6:30 PM = UTC 15:30, Jeddah 10:45 PM = UTC 19:45

TELEGRAM_BOT_TOKEN="7990089510:AAHf4U3FLQugxMxZmKoQkP5e04rn8akkPPw"
TELEGRAM_USER_ID="1514810057"
MESSAGE="${1:-Reminder (no message provided)}"

curl -s --max-time 10 -X POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d "chat_id=${TELEGRAM_USER_ID}" \
  --data-urlencode "text=${MESSAGE}" \
  -d "parse_mode=Markdown" \
  > /var/log/openclaw-reminders.log 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sent reminder: $MESSAGE" >> /var/log/openclaw-reminders.log
