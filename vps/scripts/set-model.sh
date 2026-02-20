#!/usr/bin/env bash
# set-model.sh — Update the primary model in openclaw.json and restart gateway
# Usage: /root/scripts/set-model.sh "openrouter/moonshotai/kimi-k2.5"
# Usage: /root/scripts/set-model.sh --free   (switch to free model mode)

set -euo pipefail

CONFIG_FILE="/root/.openclaw/openclaw.json"
MODE="${1:?Usage: $0 <model-id|--free>}"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: $CONFIG_FILE not found"
  exit 1
fi

if [ "$MODE" = "--free" ]; then
  PRIMARY="openrouter/stepfun/step-3.5-flash:free"
  FALLBACKS='["openrouter/upstage/solar-pro-3:free","openrouter/qwen/qwq-32b:free"]'
  LABEL="free mode (step-3.5-flash:free)"
else
  PRIMARY="$MODE"
  FALLBACKS='["openrouter/deepseek/deepseek-v3.2","openrouter/qwen/qwen3.5-plus-02-15"]'
  LABEL="$PRIMARY"
fi

echo "Switching primary model to: $LABEL"

# Update model in config using python3 (handles JSON with comments via pre-strip)
python3 - "$CONFIG_FILE" "$PRIMARY" "$FALLBACKS" << 'PYEOF'
import sys, json, re

config_file = sys.argv[1]
primary = sys.argv[2]
fallbacks_str = sys.argv[3]

with open(config_file, 'r') as f:
    raw = f.read()

# Strip JS-style // comments before parsing (negative lookbehind for : to preserve URLs)
raw_clean = re.sub(r'(?<!:)//[^\n]*', '', raw)
config = json.loads(raw_clean)

config.setdefault('agents', {}).setdefault('defaults', {})['model'] = {
    'primary': primary,
    'fallbacks': json.loads(fallbacks_str)
}

with open(config_file, 'w') as f:
    json.dump(config, f, indent=2)
print("Config updated.")
PYEOF

# Restart gateway
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
systemctl --user restart openclaw-gateway
sleep 2

status=$(systemctl --user is-active openclaw-gateway 2>/dev/null || echo "unknown")
echo "Gateway status: $status"
echo "Done. Active model: $LABEL"
