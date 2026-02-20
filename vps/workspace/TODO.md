# TODO.md — Task Queue

Format: `- [ ] task` / `- [x] task (completed YYYY-MM-DD)`
Add new tasks at the bottom of the active section.
Never delete completed tasks — mark them done and move to the completed section.

---

## Active

- [ ] Get Telegram forum group ID — send /chatid in the forum group to discover it
- [ ] Complete Gmail OAuth setup (see telegram-router/docs/OAUTH_SETUP_GUIDE.md Part 1)
- [ ] Complete Microsoft OAuth setup (see telegram-router/docs/OAUTH_SETUP_GUIDE.md Part 2)
- [ ] Set TELEGRAM_FORUM_ID in .env and run ./telegram-router/setup.sh
- [ ] Install ClawGuard: npm install -g clawguard on VPS

---

## Backlog

- [ ] Build cron job monitoring system with Telegram topic for output/failures
- [ ] Set up daily free-model scanner (cron, not AI — fetch OpenRouter API)
- [ ] Set up r/openclaw Reddit scanner (every 2 days, post highlights to Telegram)
- [ ] Design memory upgrade: short-term (7 days) → long-term (pgvector summaries)
- [ ] Write custom SKILL.md files for: dev workflow, finance, travel planning
- [ ] Set up GitHub Actions to sync openclaw config to VPS on push

---

## Completed

- [x] VPS provisioned and secured (2026-02-20)
- [x] Tailscale connected at 100.117.169.56 (2026-02-20)
- [x] SSH restricted to Tailscale only (2026-02-20)
- [x] openclaw installed, gateway running as systemd service (2026-02-20)
- [x] Kimi K2.5 configured as default model via OpenRouter (2026-02-20)
- [x] Brave search enabled (2026-02-20)
- [x] Telegram bot @openclaw_vps_eric_bot live (2026-02-20)
- [x] PostgreSQL on VPS with project_topics table (2026-02-20)
- [x] telegram-router codebase built (2026-02-20)
- [x] OAuth setup scripts written (2026-02-20)
- [x] GitHub repo created (2026-02-20)
