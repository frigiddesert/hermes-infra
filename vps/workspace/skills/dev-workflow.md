# SKILL: Developer Workflow

## When to use this skill
When Eric asks about code, servers, git, deployments, debugging, or anything technical.

## Eric's Stack
- **Infra**: Proxmox, Ubuntu servers, Tailscale (full mesh network)
- **Self-hosted**: Coolify, n8n, NocoDB, Postiz, Immich, OpenWebUI
- **Languages**: Prefers shell scripts for automation; uses whatever fits the job
- **VPS**: openclaw-vps @ 100.117.169.56 (Tailscale), 23.94.122.175 (public)
- **Database on VPS**: PostgreSQL (claude_router db), SQLite (openclaw memory)
- **Git**: frigiddesert on GitHub, private repos

## Core Principles
- **Eric doesn't want CLI commands** — he tells you what he needs, you figure out how
- Always check if something is running before trying to start it
- Prefer idempotent operations (run twice = same result)
- Log everything, especially errors

## Common Tasks

### Check a service
```bash
systemctl status <service>
journalctl -u <service> -n 50
```

### Restart openclaw gateway
```bash
systemctl --user restart openclaw-gateway
# Check: openclaw gateway call health --token $(grep token ~/.openclaw/openclaw.json | tail -1 | tr -d ' ",')
```

### Deploy to VPS
Use the sync script from local:
```bash
./scripts/sync-to-vps.sh
```

### Quick file edit on VPS
```bash
ssh openclaw 'nano /path/to/file'
# Or: ssh openclaw "sed -i 's/old/new/g' /path/to/file"
```

### Check disk/memory
```bash
df -h / && free -h
```

### View cron logs
```bash
tail -f /var/log/openclaw-watchdog.log
tail -f /var/log/openclaw-heartbeat.log
tail -f /var/log/openclaw-cron.log
```

## Setting Reminders (Zero-Token)
```bash
# Convert to UTC first (Jeddah=UTC+3, EST=UTC-5)
/root/scripts/add-reminder.sh "30 15 20 2 *" "Your reminder message"
```
Never use sleep or background processes for reminders — always use cron.

## Model Switching
```bash
/root/scripts/set-model.sh "openrouter/moonshotai/kimi-k2.5"
/root/scripts/set-model.sh --free   # switch to free mode
```

## Debugging Tips
- Gateway not responding? Check: `systemctl --user status openclaw-gateway`
- Ollama slow? Check memory: `free -h` — may need to free RAM
- Tailscale issue? `tailscale status` and `tailscale ping openclaw-vps`
- PostgreSQL down? `systemctl status postgresql` then check `journalctl -u postgresql -n 20`
