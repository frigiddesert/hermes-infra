# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

Config-as-code for Eric's personal OpenClaw AI infrastructure on a VPS (`openclaw-vps`, `ssh openclaw`, Tailscale 100.117.169.56). It has three main components:

1. **`vps/`** — Workspace files synced to `/root/.openclaw/workspace/` on the VPS. These are the LLM's "brain": identity, memory, task queue, model reference, heartbeat instructions, and skill scripts.
2. **`telegram-router/`** — Python Telegram bot that routes messages between Telegram forum topics and tmux-hosted Claude sessions on the VPS.
3. **`cloudflare-worker/`** — Cloudflare Worker acting as a VPS watchdog (heartbeat check, command queue via KV, Telegram webhook).

## Key Commands

```bash
# Sync workspace files to VPS (also pulls back MEMORY.md, TODO.md, PROGRESS-LOG.md and commits)
./scripts/sync-to-vps.sh

# SSH to VPS
ssh openclaw

# Restart the OpenClaw gateway
ssh openclaw 'systemctl --user restart openclaw-gateway'

# Watch gateway logs
ssh openclaw 'journalctl --user -u openclaw-gateway -f'

# Telegram router — setup (creates .venv and installs systemd services)
cd telegram-router && ./setup.sh

# Telegram router — restart services on VPS
ssh openclaw 'systemctl --user restart telegram-bot telegram-session-relay telegram-inbox-watcher'
```

## Architecture

### VPS Services (systemd --user, running as root)
- `openclaw-gateway` — OpenClaw gateway on port 18789, config at `~/.openclaw/openclaw.json`
- `telegram-bot` — Python bot, routes Telegram → tmux sessions
- `telegram-session-relay` — polls Claude `.jsonl` output files, pushes assistant replies to Telegram
- `telegram-inbox-watcher` — inotify on `~/.claude/inbox/`, injects files into tmux as Claude input
- `litellm-proxy` — local LLM proxy bound to `127.0.0.1` (not public)

### Telegram Router Flow
```
Telegram message → bot.py → handlers.py
  → looks up thread_id in PostgreSQL (project_topics table)
  → sends text to tmux session via `tmux send-keys`
  → session_relay.py polls ~/.claude/projects/<hash>/conversations/*.jsonl
  → extracts assistant text → sends back to Telegram thread
```

### Database
PostgreSQL on VPS. Only table: `project_topics (project_name TEXT, topic_id BIGINT)` — maps Claude project names to Telegram forum thread IDs.

### Cloudflare Worker (`cloudflare-worker/`)
- Stores heartbeat data in KV (`heartbeat:latest`)
- Command queue in KV (`commands:queue`) — VPS polls on each heartbeat and executes queued commands
- Deploy: `cd cloudflare-worker && npx wrangler deploy`

### OpenClaw Config (`~/.openclaw/openclaw.json` on VPS — never committed)
- Gateway port 18789
- Primary model: `qwen/qwen3.5-flash-02-23` (1M context, via OpenRouter)
- Fallbacks: `z-ai/glm-4.7-flash`, `groq/llama-3.3-70b-versatile`, `bailian/qwen3-235b-a22b-instruct-2507`
- Aliases: `qflash` → qwen3.5-flash, `glm` → glm-4.7-flash
- Template at `vps/openclaw.json.example`

### Cron Scripts (`vps/cron-scripts/` in repo → `/root/.openclaw/cron-scripts/` on VPS)
- `health-check.sh` — disk/memory alerts to Discord (every 5 min)
- `saudi-brief.py` — Arabic news RSS → OpenRouter LLM → Discord morning brief (daily 6am local)
- `tz-switch.sh` — one-time timezone switch for brief schedule (March 11: Vienna→Denver)

Secrets in scripts are loaded dynamically from `~/.openclaw/openclaw.json` at runtime — not hardcoded.

### Workspace Files (`vps/workspace/`)
Synced to VPS via `sync-to-vps.sh`. The LLM reads these on each session:
- `SOUL.md` — identity, decision loop, core truths, continuity rules
- `AGENTS.md` — behavioral protocols: WAL, anti-loop, prompt injection defense, memory tiers
- `SESSION-STATE.md` — active working memory (WAL write target, survives compaction)
- `MEMORY.md` — long-term facts (pulled back from VPS on sync)
- `HEARTBEAT.md` — what the LLM does on each heartbeat trigger
- `TODO.md` / `ACTIVE-TASK.md` / `PROGRESS-LOG.md` — task management
- `MODELS.md` — model reference list with free/paid labels
- `skills/` — task-specific skill files loaded on demand

## Secrets

Never committed. On VPS only:
- `~/.openclaw/openclaw.json` (real config with API keys)
- `~/.openclaw/credentials/` (OAuth tokens)
- `telegram-router/.env` (`TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, `TELEGRAM_FORUM_ID`, `ADMIN_TOKEN`)

## Firewall Notes

The VPS uses `iptables` directly (UFW was removed when `iptables-persistent` was installed):
- SSH (port 22): Tailscale-only (`100.64.0.0/10`)
- Docker-exposed ports (3001, 3214, 9998, 9003): Tailscale-only via `DOCKER-USER` chain
- `litellm-proxy`: bound to `127.0.0.1` only
- Rules persisted in `/etc/iptables/rules.v4`
