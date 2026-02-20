#!/usr/bin/env bash
# setup.sh — Install dependencies and register systemd user services
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

echo "=== Claude Telegram Router Setup ==="
echo "Directory: $SCRIPT_DIR"

# ── Python venv ───────────────────────────────────────────────────────────────
if [ ! -d "$VENV" ]; then
    echo "[1/4] Creating virtual environment..."
    python3 -m venv "$VENV"
fi

echo "[2/4] Installing dependencies..."
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
echo "      Done."

# ── .env check ────────────────────────────────────────────────────────────────
if ! grep -q "^TELEGRAM_FORUM_ID=[0-9-]" "$SCRIPT_DIR/.env" 2>/dev/null; then
    echo ""
    echo "⚠️  TELEGRAM_FORUM_ID is not set yet."
    echo "   1. Add this bot to your Telegram forum group"
    echo "   2. Start the bot manually:  $VENV/bin/python -m telegram_bot.bot"
    echo "   3. Send /chatid in your forum group"
    echo "   4. Copy the Chat ID into .env:  TELEGRAM_FORUM_ID=<number>"
    echo "   5. Re-run this script to install services."
    echo ""
fi

# ── Systemd user services ─────────────────────────────────────────────────────
echo "[3/4] Installing systemd user services..."
mkdir -p "$SYSTEMD_USER_DIR"

for svc in claude-telegram-bot claude-session-relay claude-inbox-watcher claude-permission-relay; do
    cp "$SCRIPT_DIR/systemd/${svc}.service" "$SYSTEMD_USER_DIR/${svc}.service"
    echo "      Installed ${svc}.service"
done

systemctl --user daemon-reload

for svc in claude-telegram-bot claude-session-relay claude-inbox-watcher claude-permission-relay; do
    systemctl --user enable "$svc"
done

echo "[4/4] Enabling user lingering..."
loginctl enable-linger "$USER" 2>/dev/null || true

echo ""
echo "=== Setup complete ==="
echo ""
echo "Start all services:   systemctl --user start claude-telegram-bot claude-session-relay claude-inbox-watcher claude-permission-relay"
echo "Check status:         systemctl --user status claude-telegram-bot"
echo "Watch logs:           journalctl --user -u claude-telegram-bot -f"
echo ""
echo "Once TELEGRAM_FORUM_ID is set and services are running, try sending a"
echo "message in a forum topic that matches a tmux session name."
