# openclaw-eric

Private config-as-code repo for Eric's openclaw setup.

## What's Here

```
vps/
  openclaw.json.example   # Sanitized VPS config template
  workspace/
    SOUL.md               # Bot identity + decision loop
    MEMORY.md             # Long-term facts about Eric + infrastructure
    TODO.md               # Task queue
    ACTIVE-TASK.md        # Current task working memory
    PROGRESS-LOG.md       # Append-only completion log
    HEARTBEAT.md          # Heartbeat instructions
    MODELS.md             # Model reference with free/paid labels
    memory/               # Daily logs (YYYY-MM-DD.md)
  skills/                 # Custom SKILL.md files (audited before use)

telegram-router/          # Telegram ↔ Claude tmux session routing
  core/topic_manager.py
  telegram_bot/
  scripts/
  systemd/
  docs/OAUTH_SETUP_GUIDE.md

docs/                     # Setup and reference docs
```

## Secrets

Never committed. Stored on the VPS only:
- `~/.openclaw/openclaw.json` (real config with keys)
- `~/.openclaw/credentials/` (OAuth tokens)
- `telegram-router/.env` (bot token, DB URL, etc.)

Use the `.example` files as templates.

## VPS

- Host: `openclaw-vps` — `100.117.169.56` (Tailscale)
- SSH: `ssh openclaw` (Tailscale-only, key auth)
- Bot: `@openclaw_vps_eric_bot`
- Model: Kimi K2.5 via OpenRouter

## Quick Commands

```bash
# SSH to VPS
ssh openclaw

# Check gateway status
ssh openclaw 'openclaw gateway call health --token $(grep token ~/.openclaw/openclaw.json | tail -1 | tr -d " \",")'

# Restart gateway
ssh openclaw 'systemctl --user restart openclaw-gateway'

# Watch gateway logs
ssh openclaw 'journalctl --user -u openclaw-gateway -f'

# Deploy workspace files to VPS
./scripts/sync-to-vps.sh
```
