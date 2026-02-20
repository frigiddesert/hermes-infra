#!/usr/bin/env bash
# add-reminder.sh — Add a one-shot cron reminder (called by openclaw agent)
#
# Usage: /root/scripts/add-reminder.sh "<cron-time>" "<message>"
# where <cron-time> is a 5-field cron spec IN UTC
#
# Examples:
#   /root/scripts/add-reminder.sh "30 15 20 2 *" "Call window opens: 6:30 PM Jeddah"
#   /root/scripts/add-reminder.sh "45 19 20 2 *" "Airport window: 10:45 PM Jeddah"
#
# The openclaw agent should:
# 1. Convert user time (any timezone) to UTC
# 2. Call this script with the UTC cron time + a clear message
# 3. The script auto-removes the cron entry after it fires (using a wrapper)
#
# Timezone reference (VPS = UTC):
#   Jeddah (AST) = UTC+3  (subtract 3h to get UTC)
#   New York (EST) = UTC-5 (add 5h to get UTC)
#   New York (EDT) = UTC-4 (add 4h to get UTC)

set -euo pipefail

CRON_TIME="${1:?Usage: $0 '<cron-time>' '<message>'}"
MESSAGE="${2:?Usage: $0 '<cron-time>' '<message>'}"

# Escape the message for safe cron inclusion
SAFE_MSG=$(printf '%s' "$MESSAGE" | sed "s/'/'\\''/g")

# The cron entry: run remind.sh then remove itself from crontab
ENTRY="${CRON_TIME} /root/scripts/remind.sh '${SAFE_MSG}' && crontab -l | grep -vF '${SAFE_MSG}' | crontab -"

# Add to crontab
current=$(crontab -l 2>/dev/null || echo "")
(echo "$current"; echo "$ENTRY") | crontab -

echo "Reminder scheduled: [${CRON_TIME}] UTC → '${MESSAGE}'"
echo "Current crontab:"
crontab -l
