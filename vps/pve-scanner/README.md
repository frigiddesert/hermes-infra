# PVE Scanner — heimdall issue #35

Weekly log sweep (every 30 min) + config drift check (weekly) for Proxmox hosts.
Runs from openclaw-vps cron.

## Hosts
| Host | Tailscale IP | Description |
|------|--------------|-------------|
| pve-2 | 100.126.92.41 | Primary hypervisor (CT 109 OpenWebUI, CT 120 Hermes agent) |
| mosthutte | 100.113.108.73 | Secondary (HAOS VM 100, CT 102 Frigate) |

Source of truth: `~/code/infra-pve-2/CLAUDE.md`

## Modules

### sweep.py (every 30 min)
SSH to each host → `journalctl --since <cursor> -o json` for sshd, pveproxy, pvedaemon, pve-firewall.
Rules:
- `auth_anomaly`: >10 failed SSH auth or >3 unique IPs
- `auth_anomaly`: PVE API auth failures
- `boundary_failure`: service crash loops (>2 restarts/window)
- `boundary_failure`: disk >90% on `/`, `rpool`, `pve-root`
- `boundary_failure`: OOM kills

State: per-host `state/sweep_state.json` (cursor + rolling baselines)

### posture.py (weekly)
SSH to each host → snapshot:
- `pct list -o json` (containers)
- `qm list -o json` (VMs)
- `pve-firewall compile --output-format json` (firewall rules)
- `/root/.ssh/authorized_keys` (SHA256 fingerprints)

Diff vs `posture-baseline.json`. Findings → `config_drift` events.
Human accepts changes by leaving baseline updated; reverts if unintended.

## First-run seeding
Both modules: first run pages only the 5 most severe findings; rest seeded as known (`apply_first_run_seeding`).

## Setup (one-time, human)

1. **SSH access**: Add openclaw-vps's SSH pubkey to `root@100.126.92.41` and `root@100.113.108.73` `authorized_keys`.
   - Read-only intent — scanner never mutates PVE or guest state (Constitution II.1)
   - Verify: `ssh root@100.126.92.41 echo ok` and `ssh root@100.113.108.73 echo ok`

2. **Deploy to openclaw-vps**:
   ```bash
   rsync -av /home/eric/code/openclaw/vps/pve-scanner/ openclaw:/root/pve-scanner/
   chmod 600 /root/pve-scanner/.env  # after populating HEIMDALL_SERVICE_KEY
   ```

3. **Cron on openclaw-vps** (as root):
   ```
   # Log sweep every 30 min
   */30 * * * * cd /root/pve-scanner && python3 run_scan.py sweep >> /root/pve-scanner/sweep.log 2>&1

   # Config drift weekly (Sunday 03:00)
   0 3 * * 0 cd /root/pve-scanner && python3 run_scan.py posture >> /root/pve-scanner/posture.log 2>&1
   ```

4. **Heartbeat**: The openclaw-vps app already reports a heartbeat (issue #25). The sweep logs serve as its own liveness; if sweeps stop, the heartbeat will alert.

## Running manually
```bash
# Sweep (30-min job)
python3 run_scan.py sweep

# Posture (weekly job)
python3 run_scan.py posture

# Dry run (no hub posts)
python3 run_scan.py sweep --dry-run
python3 run_scan.py posture --dry-run
```

## Testing
```bash
python3 -m pytest tests/ -v
```

## Files
- `common.py` — shared helpers (state, ingest, SSH, secrets)
- `sweep.py` — log sweep rules (auth, crash, disk, OOM)
- `posture.py` — config drift (inventory, firewall, SSH keys)
- `run_scan.py` — entrypoint (sweep|posture)
- `scanner-config.yaml` — thresholds & host config
- `posture-baseline.json` — committed baseline (updated on accepted changes)
- `.env.example` — copy to `/root/pve-scanner/.env`, add `HEIMDALL_SERVICE_KEY`

## Related
- heimdall issue #35 (this scanner)
- heimdall issue #24 (security-scanner/cf_posture.py pattern)
- heimdall issue #25 (log-sweep/sweep.py pattern)
- Constitution §IV (proactive defense)