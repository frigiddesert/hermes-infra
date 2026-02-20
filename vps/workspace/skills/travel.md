# SKILL: Travel & Timezone

## Eric's Travel Context
- **Home base**: Jeddah, Saudi Arabia (AST = UTC+3)
- **Frequently visits**: Al Balad (Jeddah old city), other KSA cities
- **International**: Travels to US and Europe regularly

## Timezone Quick Reference
| Location | Zone | UTC Offset |
|---|---|---|
| Jeddah / Riyadh | AST | UTC+3 |
| New York (winter) | EST | UTC-5 |
| New York (summer) | EDT | UTC-4 |
| London (winter) | GMT | UTC+0 |
| London (summer) | BST | UTC+1 |
| Dubai | GST | UTC+4 |
| Paris (winter) | CET | UTC+1 |

## Conversion Examples
- Jeddah 9 AM → New York (EST): subtract 8h → 1 AM prior day
- New York 10 AM EST → Jeddah: add 8h → 6 PM same day
- Jeddah time to UTC: subtract 3h

## Setting Call Reminders
1. Convert to UTC first (VPS runs UTC)
2. Use: `/root/scripts/add-reminder.sh "<UTC cron>" "message"`

```bash
# Example: 6:30 PM Jeddah = 15:30 UTC
/root/scripts/add-reminder.sh "30 15 20 2 *" "📞 Call window open! 6:30-7:05 PM Jeddah"
```

## Flight & Travel Helpers
- For flight times: always confirm timezone of departure AND arrival airports
- Common layover hubs: Dubai (DXB), Doha (DOH), Istanbul (IST), London (LHR)
- Jeddah airport: King Abdulaziz International (JED)

## Prayer Times (Jeddah approximate — varies by date)
Ask Eric if he needs these; don't add them without being asked.

## Travel Tips for Eric
- When asking "what time should I call X?", always:
  1. State both local times clearly
  2. Ask if a reminder should be set
  3. If yes, set the cron reminder immediately (don't wait)
