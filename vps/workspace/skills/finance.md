# SKILL: Finance & Business

## Eric's Financial Context
- Based in Saudi Arabia — primary currency SAR (1 USD ≈ 3.75 SAR, fixed peg)
- Likely has US financial accounts as well
- Business owner / entrepreneur (runs multiple self-hosted services)

## Core Finance Tasks

### Currency Conversion
SAR to USD: divide by 3.75
USD to SAR: multiply by 3.75
For other currencies, use web search for live rates.

```
# Quick conversion
echo "scale=2; 1000 / 3.75" | bc   # SAR to USD
echo "scale=2; 100 * 3.75" | bc    # USD to SAR
```

### Expense Tracking
If Eric wants to log expenses, append to a file:
```bash
echo "$(date '+%Y-%m-%d') | $AMOUNT $CURRENCY | $CATEGORY | $NOTE" >> ~/expenses.csv
```
Categories: food, transport, accommodation, tech, subscription, other

### Subscription Audit
When asked, list known recurring costs:
- OpenRouter API usage (pay-per-token)
- RackNerd VPS (~$XX/year)
- Tailscale (free tier or paid)
- Various SaaS tools

### Business Automation Ideas
- n8n (self-hosted) for workflow automation
- NocoDB for database-as-spreadsheet
- Postiz for social media scheduling

## Market Data
Use Brave web search to get current:
- Stock prices, crypto prices
- Currency rates
- Economic news

## Financial Principles (don't assume — ask first)
- Never make investment decisions for Eric
- Present options with pros/cons, let Eric decide
- Flag recurring costs that seem high or unused

## Common Requests
- "How much is X in SAR/USD?" → convert with 3.75 rate
- "Track this expense" → append to expenses.csv
- "What's my OpenRouter spend?" → check account dashboard (need URL from Eric)
