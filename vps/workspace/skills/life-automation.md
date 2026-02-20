# SKILL: Life Automation

## Philosophy
Eric wants FRICTIONLESS interaction. He says what he needs in plain language.
The bot figures out HOW without Eric needing to know CLI commands or technical details.

## Reminders & Scheduling (Zero-Token Cron)

### Set a one-time reminder
```bash
# Always convert to UTC first!
/root/scripts/add-reminder.sh "<min hour day month *>" "message"
# Example (6:30 PM Jeddah = 15:30 UTC):
/root/scripts/add-reminder.sh "30 15 20 2 *" "Time to do the thing!"
```

### Set a recurring reminder
```bash
# Add directly to crontab with the reminder script
(crontab -l; echo "0 6 * * 1 /root/scripts/remind.sh 'Monday morning check-in'") | crontab -
```

### List active reminders
```bash
crontab -l | grep remind.sh
```

### Cancel a reminder
```bash
crontab -l | grep -v "your reminder text" | crontab -
```

## Task Management
Eric uses TODO.md on the VPS. Pattern:
- Add item: append to `~/.openclaw/workspace/TODO.md`
- Complete item: move to completed section with date
- Always read current TODO.md before adding duplicates

## Memory & Notes
- Permanent facts → `~/.openclaw/workspace/MEMORY.md`
- Daily log → `~/.openclaw/workspace/memory/$(date +%Y-%m-%d).md`
- Active task → `~/.openclaw/workspace/ACTIVE-TASK.md`
- Completed log → `~/.openclaw/workspace/PROGRESS-LOG.md`

## Common Automation Patterns

### "Remind me every day at X"
```bash
# 8 AM Jeddah = 5 AM UTC
(crontab -l; echo "0 5 * * * /root/scripts/remind.sh 'Daily reminder text'") | crontab -
```

### "Tell me when X is done"
Use the watchdog notification pattern:
```bash
# At end of long-running script:
/root/scripts/remind.sh "✅ Task complete: X is done"
```

### "Check on this regularly"
Create a cron script in `/root/scripts/` using the `cron-run.sh` wrapper:
```bash
*/30 * * * * /root/scripts/cron-run.sh my-check /root/scripts/my-check.sh
```

## Email (When OAuth is Set Up)
Gmail + Outlook OAuth tokens stored at `~/.openclaw/credentials/`
- Read email: use IMAP with token
- Send email: use SMTP/API with token
- Calendar: use Google Calendar API or Microsoft Graph API

## Contact & Communication
- Telegram: primary channel (Jeddah timezone aware)
- Email: pending OAuth setup
- Calendar: pending OAuth setup
