# Zero-to-Hero OpenClaw VPS Setup Prompt
### For an AI coding/devops agent — complete reproducible setup

---

## YOUR MISSION

Set up a production-ready personal AI assistant server from a bare Ubuntu 24.04 VPS.
At the end, the operator receives a Telegram message confirming the bot is live.

Work sequentially. Verify each step before moving on. If something fails, diagnose and fix
before continuing — do not skip steps.

---

## INPUTS — Fill These In Before Starting

```
VPS_IP=<public IP of the VPS>
VPS_ROOT_PASSWORD=<root password from provider>
VPS_SSH_PORT=22

# SSH public key to install for passwordless root login
# (the private key must be on the operator's local machine)
OPERATOR_SSH_PUBKEY="ssh-ed25519 AAAA... your-key-name"

# Tailscale auth key — generate at https://tailscale.com/admin/settings/keys
# Use a reusable key if setting up multiple machines
TAILSCALE_AUTH_KEY="tskey-auth-..."

# Telegram bot — create via @BotFather on Telegram
TELEGRAM_BOT_TOKEN="<bot-id>:<token>"
# Telegram user ID of the operator (only this ID can interact with the bot)
# Find yours: message @userinfobot on Telegram
TELEGRAM_OPERATOR_ID=<numeric user ID>

# OpenRouter API key — https://openrouter.ai/keys
OPENROUTER_API_KEY="sk-or-v1-..."

# Brave Search API key — https://brave.com/search/api/
# Free tier: 2,000 searches/month
BRAVE_API_KEY="..."

# PostgreSQL credentials (you will create these during setup)
PG_DB=claude_router
PG_USER=openclaw
PG_PASSWORD=<generate a strong random password>

# GitHub repo for config-as-code (create before running or let agent create it)
GITHUB_REPO="<username>/openclaw-<name>"  # e.g. frigiddesert/openclaw-prod
GITHUB_TOKEN="ghp_..."  # needs repo scope

# Local SSH alias for the VPS (used in ~/.ssh/config)
SSH_ALIAS=openclaw

# Operator name (used in bot greeting and memory files)
OPERATOR_NAME="<your name>"
```

---

## PHASE 1 — Local Machine Preparation

Do this on the **operator's local machine** before touching the VPS.

### 1.1 — Verify SSH key exists locally
```bash
ls ~/.ssh/id_ed25519_server* 2>/dev/null || \
  ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_server -C "server-key-$(date +%Y%m%d)" -N ""
cat ~/.ssh/id_ed25519_server.pub  # this is OPERATOR_SSH_PUBKEY
```

### 1.2 — Add SSH config entry
Append to `~/.ssh/config`:
```
Host $SSH_ALIAS
    HostName $VPS_IP          # will be updated to Tailscale IP after Phase 2
    User root
    IdentityFile ~/.ssh/id_ed25519_server
    IdentitiesOnly yes
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

### 1.3 — Test initial password-based connectivity
```bash
# Verify the VPS is reachable (use sshpass or expect, or just confirm manually)
nc -z -w5 $VPS_IP 22 && echo "VPS reachable" || echo "CANNOT REACH VPS — stop"
```

---

## PHASE 2 — VPS Initial Access & SSH Hardening

Connect using password auth for the first time.

### 2.1 — Install operator SSH key
```bash
ssh-copy-id -i ~/.ssh/id_ed25519_server.pub -o StrictHostKeyChecking=no root@$VPS_IP
# Or manually:
ssh root@$VPS_IP "mkdir -p ~/.ssh && chmod 700 ~/.ssh && \
  echo '$OPERATOR_SSH_PUBKEY' >> ~/.ssh/authorized_keys && \
  chmod 600 ~/.ssh/authorized_keys"
```

### 2.2 — Verify passwordless login works
```bash
ssh -i ~/.ssh/id_ed25519_server -o BatchMode=yes root@$VPS_IP "echo OK"
# Must print OK — if not, stop and diagnose
```

### 2.3 — Harden SSH config on VPS
SSH in and write `/etc/ssh/sshd_config`:
```
Port 22
Protocol 2
HostKey /etc/ssh/ssh_host_ed25519_key
HostKey /etc/ssh/ssh_host_rsa_key

PermitRootLogin prohibit-password
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
PasswordAuthentication no
ChallengeResponseAuthentication no
UsePAM yes

LoginGraceTime 30
MaxAuthTries 3
MaxSessions 10
MaxStartups 10:30:100

AllowAgentForwarding no
AllowTcpForwarding no
X11Forwarding no
PrintMotd no

ClientAliveInterval 300
ClientAliveCountMax 2

SyslogFacility AUTH
LogLevel VERBOSE

AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/openssh/sftp-server
```

After writing: `systemctl reload ssh`

Do NOT add `ListenAddress` yet — Tailscale isn't installed yet.

---

## PHASE 3 — System Update & Packages

```bash
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq ufw fail2ban curl wget git unzip build-essential
```

---

## PHASE 4 — Tailscale

### 4.1 — Install
```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

### 4.2 — Connect to tailnet
```bash
tailscale up --authkey=$TAILSCALE_AUTH_KEY --hostname=openclaw-vps
```

### 4.3 — Get Tailscale IP
```bash
TAILSCALE_IP=$(tailscale ip -4)
echo "Tailscale IP: $TAILSCALE_IP"
# Note this IP — it replaces VPS_IP for all future SSH connections
```

### 4.4 — Lock SSH to Tailscale only
Append to `/etc/ssh/sshd_config`:
```
ListenAddress $TAILSCALE_IP
ListenAddress 127.0.0.1
```
Then: `systemctl restart ssh`

**Immediately update local `~/.ssh/config`** to use `HostName $TAILSCALE_IP`

### 4.5 — Verify SSH still works via Tailscale
```bash
ssh $SSH_ALIAS "echo 'Tailscale SSH works'"
# If this fails: you still have the old session open — fix before closing it
```

---

## PHASE 5 — Firewall & Security

### 5.1 — UFW
```bash
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow in on tailscale0                          # all Tailscale traffic
ufw allow in on tailscale0 to any port 22 proto tcp # SSH via Tailscale only
ufw --force enable
```

### 5.2 — fail2ban
Write `/etc/fail2ban/jail.local`:
```ini
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5
backend  = systemd

[sshd]
enabled  = true
port     = ssh
logpath  = /var/log/auth.log
maxretry = 3
bantime  = 24h
```
Then: `systemctl enable fail2ban && systemctl restart fail2ban`

### 5.3 — Kernel hardening
Write `/etc/sysctl.d/99-hardening.conf`:
```ini
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.conf.all.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_max_syn_backlog = 2048
net.ipv4.tcp_synack_retries = 2
net.ipv4.tcp_syn_retries = 5
net.ipv4.conf.all.log_martians = 1
net.ipv4.tcp_rfc1337 = 1
```
Then: `sysctl -p /etc/sysctl.d/99-hardening.conf`

---

## PHASE 6 — Node.js

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y nodejs
node --version  # must be v22.x
```

---

## PHASE 7 — Ollama (Local Embeddings)

```bash
curl -fsSL https://ollama.com/install.sh | sh
systemctl enable ollama
systemctl start ollama
sleep 5
ollama pull nomic-embed-text
# Verify:
ollama list | grep nomic-embed-text
```

---

## PHASE 8 — PostgreSQL

### 8.1 — Install
```bash
apt-get install -y postgresql postgresql-contrib
systemctl enable postgresql
systemctl start postgresql
```

### 8.2 — Create DB, user, and table
```bash
sudo -u postgres psql << SQL
CREATE USER $PG_USER WITH PASSWORD '$PG_PASSWORD';
CREATE DATABASE $PG_DB OWNER $PG_USER;
\c $PG_DB
CREATE TABLE IF NOT EXISTS project_topics (
    project_name VARCHAR(100) PRIMARY KEY,
    topic_id     INTEGER NOT NULL,
    created_at   TIMESTAMP DEFAULT NOW()
);
GRANT ALL PRIVILEGES ON TABLE project_topics TO $PG_USER;
SQL
```

### 8.3 — Expose on Tailscale network
```bash
PG_VERSION=$(pg_lsclusters -h | awk '{print $1}' | head -1)
sed -i "s/^#*listen_addresses = .*/listen_addresses = 'localhost,$TAILSCALE_IP'/" \
  /etc/postgresql/$PG_VERSION/main/postgresql.conf

cat >> /etc/postgresql/$PG_VERSION/main/pg_hba.conf << 'HBA'
# Tailscale subnet
host    claude_router   openclaw        100.64.0.0/10           scram-sha-256
HBA

ufw allow in on tailscale0 to any port 5432 proto tcp comment 'PostgreSQL via Tailscale'
systemctl restart postgresql
```

### 8.4 — Verify from local machine
```bash
# Run this on the LOCAL machine (not VPS)
PGPASSWORD=$PG_PASSWORD psql -h $TAILSCALE_IP -U $PG_USER -d $PG_DB \
  -c "SELECT 'connected' AS status;"
# Must print: connected
```

---

## PHASE 9 — openclaw

### 9.1 — Install
```bash
curl -fsSL https://openclaw.ai/install.sh | bash
which openclaw  # must be /usr/bin/openclaw
```

### 9.2 — Add Telegram channel
```bash
openclaw channels add --channel telegram --token "$TELEGRAM_BOT_TOKEN"
```

### 9.3 — Write config
Write `~/.openclaw/openclaw.json`:
```json
{
  "gateway": {
    "port": 18789,
    "mode": "local"
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "openrouter/moonshotai/kimi-k2.5",
        "fallbacks": [
          "openrouter/deepseek/deepseek-v3.2",
          "openrouter/qwen/qwen3.5-plus-02-15"
        ]
      },
      "memorySearch": {
        "enabled": true,
        "provider": "openai",
        "remote": {
          "baseUrl": "http://127.0.0.1:11434/v1",
          "apiKey": "ollama"
        }
      }
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "botToken": "$TELEGRAM_BOT_TOKEN",
      "dmPolicy": "allowlist",
      "allowFrom": [$TELEGRAM_OPERATOR_ID],
      "groupPolicy": "disabled",
      "streamMode": "partial"
    }
  },
  "tools": {
    "web": {
      "search": {
        "enabled": true,
        "provider": "brave",
        "apiKey": "$BRAVE_API_KEY",
        "maxResults": 8,
        "timeoutSeconds": 30,
        "cacheTtlMinutes": 15
      }
    }
  },
  "env": {
    "OPENROUTER_API_KEY": "$OPENROUTER_API_KEY"
  },
  "plugins": {
    "entries": {
      "telegram": { "enabled": true }
    }
  }
}
```
Then: `chmod 600 ~/.openclaw/openclaw.json`

### 9.4 — Install gateway as systemd service
```bash
openclaw gateway install

# Enable linger so user services start at boot
loginctl enable-linger root

# Start gateway
systemctl --user daemon-reload
systemctl --user enable openclaw-gateway
systemctl --user start openclaw-gateway

sleep 10
systemctl --user is-active openclaw-gateway  # must say: active
```

### 9.5 — Verify gateway is healthy
```bash
GW_TOKEN=$(python3 -c "
import json, re
s = open('/root/.openclaw/openclaw.json').read()
s = re.sub(r'//[^\n]*', '', s)
s = re.sub(r',(\s*[}\]])', r'\1', s)
print(json.loads(s)['gateway']['auth']['token'])
")

openclaw gateway call health --token "$GW_TOKEN" | python3 -c "
import json,sys; d=json.load(sys.stdin)
print('Gateway OK:', d['ok'])
print('Telegram configured:', d['channels']['telegram']['configured'])
print('Bot:', d['channels']['telegram']['probe']['bot']['username'])
"
```

Expected output:
```
Gateway OK: True
Telegram configured: True
Bot: your_bot_username
```

---

## PHASE 10 — Workspace Files

Create the agent's workspace directory and core files:

```bash
mkdir -p ~/.openclaw/workspace/memory
```

### `~/.openclaw/workspace/SOUL.md`
```markdown
# SOUL.md — Who I Am and How I Work

## Identity
I am $OPERATOR_NAME's personal AI assistant, running on openclaw-vps.
I communicate via Telegram. I am powered by Kimi K2.5 via OpenRouter by default.

## Core Decision Loop

After completing any task, I always ask: what comes next?

  Receive → Understand → Plan → Act → Verify → Log → Decide next

1. Receive — understand what $OPERATOR_NAME actually needs
2. Understand — check MEMORY.md and today's log for context
3. Plan — write approach to ACTIVE-TASK.md before starting
4. Act — do the work, checkpoint progress to ACTIVE-TASK.md
5. Verify — confirm it worked; if not, try once more
6. Log — write outcome and learnings to memory/YYYY-MM-DD.md
7. Decide — add follow-up tasks to TODO.md proactively

## When I Finish a Task
- Mark done in TODO.md
- Write 2-3 sentence summary to today's log
- Tell $OPERATOR_NAME what I did and what's next

## What I Never Do Without Explicit Approval
- Send emails to anyone other than $OPERATOR_NAME
- Post to any public platform
- Execute destructive commands (rm -rf, DROP TABLE, etc.)
- Spend money or trigger purchases
- Create calendar events with external attendees

## Communication Style
- Direct and useful — no filler
- Surface the important thing first
- One message per thought
- When done: say what it is and what's next
```

### `~/.openclaw/workspace/MEMORY.md`
```markdown
# Memory — $OPERATOR_NAME's OpenClaw Bot

## About $OPERATOR_NAME
- Name: $OPERATOR_NAME
- Telegram ID: $TELEGRAM_OPERATOR_ID

## Infrastructure
- VPS: openclaw-vps @ $VPS_IP (public) / $TAILSCALE_IP (Tailscale)
- Bot Telegram: check ~/.openclaw/openclaw.json for username
- SSH: ssh $SSH_ALIAS (Tailscale-only, key auth)
- PostgreSQL: $TAILSCALE_IP:5432 / db: $PG_DB / user: $PG_USER

## Model Aliases (tell the bot which to use)
- Default: Kimi K2.5 (fast, cheap, excellent reasoning)
- "kimi-thinking" → Kimi K2 with extended thinking
- "glm" → GLM-4.7 Flash (Zhipu)
- "glm-full" → GLM-5 (744B)
- "qwen" → Qwen 3.5 Plus
- "qwen-thinking" → Qwen 3 Max thinking
- "deepseek" → DeepSeek V3.2
- "claude" → Claude Sonnet 4.6 (via OpenRouter)
- "free" → Step-3.5 Flash (free tier)

## Preferences
- Frictionless: tell the bot what you need, it figures out how
- No commands to remember — speak naturally
- Everything important written to files (memory survives context compaction)
```

### `~/.openclaw/workspace/HEARTBEAT.md`
```markdown
# Heartbeat Instructions

On each heartbeat:
1. Check for pending items in TODO.md
2. If time-sensitive (travel, reminders, deadlines) — message $OPERATOR_NAME on Telegram
3. Otherwise reply HEARTBEAT_OK silently
4. Keep heartbeat messages brief — only message if there's something worth noting
```

### `~/.openclaw/workspace/TODO.md`
```markdown
# TODO

## Active
- [ ] Operator to start bot: open Telegram → find the bot → send /start
- [ ] Set TELEGRAM_FORUM_ID for topic routing (see telegram-router setup)

## Backlog
- [ ] Gmail OAuth (docs/OAUTH_SETUP_GUIDE.md)
- [ ] Microsoft OAuth (docs/OAUTH_SETUP_GUIDE.md)
- [ ] Install ClawGuard: npm install -g clawguard
- [ ] Set up cron job monitoring with Telegram topic
- [ ] Build r/openclaw Reddit scanner (every 2 days)

## Completed
- [x] VPS provisioned and secured
- [x] Tailscale connected
- [x] openclaw installed and running
- [x] Telegram bot live
```

### `~/.openclaw/workspace/ACTIVE-TASK.md`
```markdown
# ACTIVE-TASK.md

## Current Task
None — waiting for input.

## Plan
Not started.

## Progress
Nothing yet.
```

### `~/.openclaw/workspace/PROGRESS-LOG.md`
```markdown
# PROGRESS-LOG.md — Append-Only

## Setup Complete — $(date '+%Y-%m-%d')
- VPS provisioned, secured, Tailscale connected
- openclaw installed, Kimi K2.5 via OpenRouter
- Telegram bot live, Brave search enabled
- Local embeddings: Ollama + nomic-embed-text
- PostgreSQL on VPS with project_topics table
- Outcome: ✅ Full stack operational
```

---

## PHASE 11 — Send Welcome Message

This is the verification step. If this works, everything is wired up correctly.

**First:** The operator must open Telegram, find the bot by username, and send `/start`.
The bot can only initiate messages after the user has started a conversation.

**Then run on VPS:**
```bash
openclaw agent \
  --channel telegram \
  --to $TELEGRAM_OPERATOR_ID \
  --deliver \
  --message "Hello $OPERATOR_NAME! Your openclaw bot is live on VPS ($TAILSCALE_IP). I'm running Kimi K2.5 via OpenRouter. Brave search is active. Type anything to start — I've read your memory files and I'm ready. What do you need?"
```

If this succeeds, the operator receives the message in Telegram.

---

## PHASE 12 — GitHub Repo (Config as Code)

### 12.1 — Clone and populate
```bash
# On LOCAL machine:
mkdir -p ~/code/openclaw/{vps/workspace/memory,vps/skills,telegram-router,scripts,docs}
cd ~/code/openclaw

# Pull workspace files from VPS
scp $SSH_ALIAS:/root/.openclaw/workspace/SOUL.md vps/workspace/
scp $SSH_ALIAS:/root/.openclaw/workspace/MEMORY.md vps/workspace/
scp $SSH_ALIAS:/root/.openclaw/workspace/HEARTBEAT.md vps/workspace/
scp $SSH_ALIAS:/root/.openclaw/workspace/TODO.md vps/workspace/
scp $SSH_ALIAS:/root/.openclaw/workspace/PROGRESS-LOG.md vps/workspace/
scp $SSH_ALIAS:/root/.openclaw/workspace/ACTIVE-TASK.md vps/workspace/
```

### 12.2 — Write `.gitignore`
```gitignore
.env
*_token.json
*_credentials.json
credentials/
.venv/
__pycache__/
*.pyc
*.lock
*.log
sessions/
```

### 12.3 — Create sanitized `vps/openclaw.json.example`
Copy the real config but replace all secret values with placeholders:
- `YOUR_TELEGRAM_BOT_TOKEN`
- `YOUR_BRAVE_API_KEY`
- `YOUR_OPENROUTER_API_KEY`
- `YOUR_TELEGRAM_USER_ID`

### 12.4 — Push to GitHub
```bash
cd ~/code/openclaw
git init && git branch -m main
git add -A
git commit -m "Initial commit — openclaw config as code"

# Create private repo (requires gh CLI)
gh repo create $GITHUB_REPO --private --source . --remote origin --push
```

### 12.5 — Add sync script at `scripts/sync-to-vps.sh`
```bash
#!/usr/bin/env bash
# Syncs workspace files to VPS and commits changes
rsync -av --exclude='memory/' ~/code/openclaw/vps/workspace/ $SSH_ALIAS:/root/.openclaw/workspace/
scp $SSH_ALIAS:/root/.openclaw/workspace/MEMORY.md ~/code/openclaw/vps/workspace/
scp $SSH_ALIAS:/root/.openclaw/workspace/TODO.md ~/code/openclaw/vps/workspace/
scp $SSH_ALIAS:/root/.openclaw/workspace/PROGRESS-LOG.md ~/code/openclaw/vps/workspace/
cd ~/code/openclaw && git add -A
git diff --cached --quiet || git commit -m "sync: workspace $(date '+%Y-%m-%d %H:%M')" && git push
```

---

## PHASE 13 — Telegram Router (Optional but Recommended)

The telegram-router lets Claude Code tmux sessions on your local machines communicate
bidirectionally with Telegram forum topics. Skip if not using Claude Code locally.

Source: `telegram-router/` in the GitHub repo.

### Requirements
- Python 3.11+
- The PostgreSQL DB from Phase 8
- A Telegram forum group (supergroup with Topics enabled)
- The forum group's chat ID (negative number, e.g. -1001234567890)

### Get forum group ID (pick one method)
1. **Web**: Open https://web.telegram.org → click the group → read the number from the URL
2. **API**: `curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates"` after sending a message to the group
3. **Forward**: Forward a group message to @userinfobot on Telegram

### Setup
```bash
cd ~/code/openclaw/telegram-router
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Create .env
cat > .env << ENV
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
TELEGRAM_FORUM_ID=<your forum chat id>
DATABASE_URL=postgresql://$PG_USER:$PG_PASSWORD@$TAILSCALE_IP:5432/$PG_DB
CLAUDE_PROJECTS_DIR=$HOME/.claude/projects
INBOX_DIR=$HOME/.claude/inbox
PERMISSION_DELAY=3.0
ENV

./setup.sh  # installs systemd user services
systemctl --user start claude-telegram-bot claude-session-relay claude-inbox-watcher
```

---

## VERIFICATION CHECKLIST

After completing all phases, confirm:

```bash
# On VPS:
systemctl --user is-active openclaw-gateway    # active
systemctl is-active ollama                     # active
systemctl is-active postgresql                 # active
systemctl is-active fail2ban                   # active
tailscale status | head -1                     # shows openclaw-vps connected
ss -tlnp | grep ':22' | grep -v '0.0.0.0'    # only Tailscale IP, not all interfaces
ss -tlnp | grep ':18789'                       # only 127.0.0.1 (loopback only)
ss -tlnp | grep ':5432' | grep $TAILSCALE_IP  # PostgreSQL on Tailscale

# On local machine:
ssh $SSH_ALIAS "echo works"                    # passwordless, via Tailscale
```

And the operator has received the Telegram welcome message from the bot.

---

## QUICK REFERENCE — Post-Setup Commands

```bash
# SSH to VPS
ssh $SSH_ALIAS

# Check bot health
ssh $SSH_ALIAS 'openclaw gateway call health --token \
  $(python3 -c "import json,re,sys; s=open(\"/root/.openclaw/openclaw.json\").read(); \
  s=re.sub(r\"//[^\n]*\",\"\",s); s=re.sub(r\",(\s*[}\]])\",r\"\1\",s); \
  print(json.loads(s)[\"gateway\"][\"auth\"][\"token\"])")'

# Restart bot
ssh $SSH_ALIAS 'systemctl --user restart openclaw-gateway'

# Watch bot logs
ssh $SSH_ALIAS 'journalctl --user -u openclaw-gateway -f'

# Sync workspace to/from VPS
~/code/openclaw/scripts/sync-to-vps.sh

# Send a Telegram message from CLI
ssh $SSH_ALIAS "openclaw agent --channel telegram --to $TELEGRAM_OPERATOR_ID \
  --deliver --message 'Hello from VPS'"
```

---

## WHAT WAS NOT COVERED (Future Work)

These are in the backlog but not part of this initial setup:

- **Gmail OAuth** — see `telegram-router/docs/OAUTH_SETUP_GUIDE.md`
- **Microsoft/Office 365 OAuth** — same guide
- **ClawGuard** — `npm install -g clawguard` on VPS for skill security
- **Cron job monitoring** — deterministic job runner with Telegram notifications
- **Free model scanner** — daily cron to fetch OpenRouter free models
- **r/openclaw Reddit scanner** — every 2 days, highlights to Telegram
- **Memory system upgrade** — short-term (7 days) → long-term (pgvector summaries)
- **Custom SKILL.md files** — audit any ClawHub skills before installing
- **GitHub Actions** — auto-sync config to VPS on push to main

---

*Generated from a real setup session. All steps verified on Ubuntu 24.04 LTS.*
*openclaw version: 2026.2.19-2*
