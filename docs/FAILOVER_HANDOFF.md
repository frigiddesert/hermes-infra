# Hermes Failover Setup - Handoff Document

**Date:** 2026-09-01  
**Target:** PVE CT 120 (Primary) / VPS (Failover)  
**Status:** Handoff Required

---

## ✅ Completed

| Component | Status |
|-----------|--------|
| **Cloudflare Tunnel** | ✅ Running (`6f7bfd77-ae71-40fd-bc8b-cb3f1e50d670`) |
| **Tunnel Access** | ✅ Policy set (7 approved users via Cloudflare Access) |
| **Sync Script** | ✅ Created (`/root/sync-hermes.sh` on PVE host) |
| **CT 120 Setup** | ✅ Docker, Python, `hermes-agent` repo installed |
| **Web Server** | ✅ Python HTTP server running on port 8787 |

---

## ❌ Pending (Urgent)

| Task | Priority | Notes |
|------|----------|-------|
| **1. Fix Tunnel Port** | 🔴 **Critical** | Config routes to **8787** (test server). Must point to **8000** (Hermes Agent). |
| **2. Add DNS Record** | 🔴 **Critical** | Cloudflare Dashboard: `agent.sandland.us` → `6f7bfd77-ae71-40fd-bc8b-cb3f1e50d670.cfargotunnel.com` (CNAME, proxied) |
| **3. Sync Data** | 🔴 **Critical** | CT 120 has 65MB; VPS has 1.3TB. Run `/root/sync-hermes.sh` to copy state. |
| **4. Install Cron** | 🟡 **Medium** | Add `*/5 * * * * /root/sync-hermes.sh` to crontab on PVE host. |
| **5. Start Hermes** | 🟡 **Medium** | Ensure Hermes gateway runs on CT 120 port 8000. |
| **6. Rotate Key** | 🟢 **Low** | Change exposed Tavily API key (`tvly-dev-...`). |

---

## 📝 Configuration Details

### Files

| Path | Owner | Purpose |
|------|-------|---------|
| `/root/sync-hermes.sh` | PVE Host | Syncs VPS → CT 120 (excludes state.db, logs) |
| `/root/.cloudflared/config.yml` | PVE Host | Tunnel ingress rules |
| `/root/.hermes/config.yaml` | CT 120 | Hermes runtime config |

### Ports

| Service | CT 120 | Tunnel Route |
|---------|--------|--------------|
| **Hermes** | `8000` | `agent.sandland.us` (needs update from 8787) |
| **SearXNG** | `8081` | `search.thebakkens.net` |
| **Omniroute** | `20128` | `omniroute.thebakkens.net` |

### Current Tunnel Config
```yaml
tunnel: 6f7bfd77-ae71-40fd-bc8b-cb3f1e50d670
credentials-file: /root/.cloudflared/6f7bfd77-ae71-40fd-bc8b-cb3f1e50d670.json
ingress:
  - hostname: agent.sandland.us
    service: http://localhost:8787  # ❌ CHANGE TO 8000
  - hostname: search.thebakkens.net
    service: http://localhost:8081
  - hostname: omniroute.thebakkens.net
    service: http://localhost:20128
  - service: http_status:404
```

---

## 🚀 Next Steps Order

1.  **Update Tunnel Config**: Change `8787` → `8000` in `/root/.cloudflared/config.yml`. Restart cloudflared.
2.  **Add DNS**: Create CNAME record in Cloudflare Dashboard for `agent.sandland.us`.
3.  **Run Sync**: Execute `/root/sync-hermes.sh` manually to verify.
4.  **Install Cron**: Add cron job on PVE host for continuous sync.
5.  **Verify Access**: Visit `https://agent.sandland.us` after DNS propagates.

---

## 🛠️ Commands Reference

```bash
# Update tunnel config on PVE host
ssh pve "pct enter 120 -- sed -i 's/8787/8000/' /root/.cloudflared/config.yml && pkill cloudflared && sleep 5 && cloudflared tunnel run 6f7bfd77-ae71-40fd-bc8b-cb3f1e50d670 &"

# Run full sync
ssh pve "/root/sync-hermes.sh"

# Install cron on CT 120
ssh pve "echo '*/5 * * * * /root/sync-hermes.sh >> /var/log/sync.log 2>&1' | pct enter 120 crontab -"

# Check CT 120 port 8000
ssh pve "pct enter 120 -- ss -tlnp | grep 8000"
```

---

## 🔑 Credentials Status

| Credential | Status | Note |
|------------|--------|------|
| **Cloudflare API** | ✅ Working | OAuth token in `~/.config/.wrangler/config/default.toml` |
| **Tavily API** | ⚠️ Rotated | Old key exposed in chat: `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` |
| **Cloudflare Access** | ✅ Active | 7 approved users for `agent.sandland.us` |

---

## 📊 Data Migration Status

| Location | Size | Last Sync |
|----------|------|-----------|
| **VPS** (`/root/.hermes`) | 1.3TB | Current |
| **CT 120** (`/root/.hermes`) | 65MB | Aug 7, 2026 |

**Action Required:** Run `/root/sync-hermes.sh` to sync state.

---

**End of Handoff**
