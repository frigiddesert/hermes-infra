#!/usr/bin/env bash
# reddit-scanner.sh — Fetch hot/top posts from r/openclaw and send digest to Telegram
# Cron: 0 9 */2 * * /root/scripts/reddit-scanner.sh >> /var/log/openclaw-reddit.log 2>&1
# (Every 2 days at 9 AM UTC = noon Jeddah)

set -euo pipefail

TELEGRAM_BOT_TOKEN="7990089510:AAHf4U3FLQugxMxZmKoQkP5e04rn8akkPPw"
TELEGRAM_USER_ID="1514810057"
SUBREDDIT="openclaw"
POST_LIMIT=5
LOG_PREFIX="[reddit $(date '+%Y-%m-%d %H:%M:%S')]"

send_telegram() {
  local text="$1"
  curl -s --max-time 15 -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_USER_ID}" \
    --data-urlencode "text=${text}" \
    -d "parse_mode=Markdown" \
    -d "disable_web_page_preview=true" > /dev/null 2>&1 || true
}

echo "$LOG_PREFIX Fetching r/${SUBREDDIT}..."

TMPJSON=$(mktemp)
trap 'rm -f "$TMPJSON"' EXIT

# Fetch hot posts from Reddit public JSON API (no auth needed)
curl -s --max-time 20 \
  -H "User-Agent: openclaw-vps-bot/1.0" \
  "https://www.reddit.com/r/${SUBREDDIT}/hot.json?limit=${POST_LIMIT}" \
  -o "$TMPJSON" 2>&1 || {
  echo "$LOG_PREFIX Failed to fetch Reddit"
  exit 0
}

# Parse with python3
digest=$(python3 - "$TMPJSON" "$SUBREDDIT" "$POST_LIMIT" << 'PYEOF'
import sys, json

json_file = sys.argv[1]
subreddit = sys.argv[2]
limit = int(sys.argv[3])

with open(json_file) as f:
    raw = f.read()

try:
    data = json.loads(raw)
    posts = data.get("data", {}).get("children", [])
except Exception as e:
    print(f"Parse error: {e}")
    sys.exit(0)

if not posts:
    print(f"No posts found in r/{subreddit}")
    sys.exit(0)

lines = [f"📰 *r/{subreddit} — Hot Posts*\n"]
for i, post in enumerate(posts[:limit], 1):
    p = post.get("data", {})
    title = p.get("title", "?")[:120]
    score = p.get("score", 0)
    comments = p.get("num_comments", 0)
    url = f"https://reddit.com{p.get('permalink', '')}"
    flair = p.get("link_flair_text", "")
    flair_str = f" `[{flair}]`" if flair else ""
    lines.append(f"{i}. [{title}]({url}){flair_str}\n   ⬆️ {score} | 💬 {comments}")

print("\n".join(lines))
PYEOF
) || {
  echo "$LOG_PREFIX Python parse failed"
  exit 0
}

if [ -z "$digest" ]; then
  echo "$LOG_PREFIX Empty digest, skipping"
  exit 0
fi

echo "$LOG_PREFIX Sending digest to Telegram"
send_telegram "$digest"
echo "$LOG_PREFIX Done"
