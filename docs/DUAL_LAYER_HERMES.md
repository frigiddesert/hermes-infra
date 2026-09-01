# Dual-Layer Hermes Failover System

**Status**: PVE Primary, VPS Failover

## Architecture

```
                        ┌────────────────────┐
                        │  PVE CT 121        │
                        │  Primary Hermes    │
                        │  24GB RAM          │
                        │  1Gig Fiber        │
                        └─────────┬──────────┘
                                  │
                    5-min sync    │
            ┌─────────────────────┘
            │
            ▼
┌──────────────────────┐
│  VPS (openclaw)      │
│  Failover Hermes     │
│  ~8GB RAM            │
└──────────────────────┘
```

## DNS Routes

| Domain | Points To | Purpose |
|--------|-----------|---------|
| `hermes.thebakkens.net` | PVE CT 121 | **Primary** agent |
| `omniroute.thebakkens.net` | PVE CT 121 | Routing service |
| `search.thebakkens.net` | PVE CT 121 | Search stack (SearXNG + Tavily fallback) |
| `heimdall.thebakkens.net` | VPS | Health monitoring |

## PVE CT 121 Components

- **Hermes Agent**: Full deployment from VPS (all profiles)
- **Omniroute**: Port 20128 (routing)
- **SearXNG**: Port 8081 (search)
- **Redis**: Port 6389 (cache)
- **Cloudflare Tunnel**: Routes all subdomains

## VPS Components

- **Hermes Agent**: Failover copy
- **Heimdall**: Monitors PVE primary health
- **Telegram Router**: Still active

## Sync System

**Location**: `/root/.hermes/scripts/sync-to-vps.sh` (PVE)

Runs every 5 minutes via cron. Syncs:
- `config.yaml`
- `SOUL.md`, `MEMORY.md`
- `TODO.md`, `ACTIVE-TASK.md`
- `.env`

**To test sync manually:**
```bash
ssh pve "pct exec 121 -- bash /root/.hermes/scripts/sync-to-vps.sh"
```

## Failover Detection

Heimdall monitors PVE via `HERMES_PVE_API_URL=https://hermes.thebakkens.net`.

**If PVE goes down:**
1. Heimdall sends critical alert
2. Manual switch: point DNS to VPS
3. Continue working from VPS

**To switch back (when PVE is restored):**
1. Fix PVE
2. Re-sync from VPS if needed
3. Point DNS back to PVE

## Health Checks

PVE Primary: `https://hermes.thebakkens.net/health`  
VPS Failover: `https://heimdall.thebakkens.net/health`

## Access Control

All Cloudflare domains are protected by **Cloudflare Access**.

**Add users to access list:**
1. Go to: https://one.dash.cloudflare.com/cfaccess
2. Find policy for domain
3. Add email addresses

## Environment Variables

### PVE (`/root/.env`)
```
TAVILY_API_KEY=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
SEARXNG_URL=http://localhost:8081
```

### VPS (`/root/.env`)
```
TAVILY_API_KEY=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
SEARXNG_URL=https://search.thebakkens.net
```

## Notes

- **Sync Delay**: ~5 minutes acceptable for non-critical state
- **Memory**: PVE has 128GB (vs VPS 8GB)
- **Network**: PVE has 1Gig fiber
- **Power**: Office UPS protects PVE
- **Tunnel Status**: Healthy (verified via Cloudflare dashboard)

---

**Last Updated**: 2026-09-01  
**Owner**: Eric Bakken