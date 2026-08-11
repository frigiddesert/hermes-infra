# security-scanner (heimdall issue #24 + Cloudflare posture)

Proactive security scanning for the frigiddesert fleet. Runs weekly from VPS crontab (no GitHub
Actions minutes spent). stdlib + a downloaded gitleaks binary only — no pip install.

## The one rule everything else follows

Eric: "I don't want to spend even 15 minutes a week reading security reports with no information I
need to act on." So: **a run that finds nothing new produces zero human-visible output.** The only
outputs are:
1. A genuinely NEW critical/high finding → one fingerprinted security-lane incident, POSTed to
   `heimdall-hub`'s `/ingest`. Gjallarhorn pages once; recurrence dedups silently (the hub already
   does this — every ingested security-type event pages, so THIS scanner's own state files are what
   stop it from re-posting an already-known finding on every subsequent run).
2. Everything else accumulates silently in `state/*.json` — queryable by a human on demand, never
   pushed.

## Modules

| # | Module | File | Cadence | Output type |
|---|--------|------|---------|--------------|
| 1 | Dependency scan | `dependency_scan.py` | weekly | `dependency_vuln` |
| 2 | Secret scan (gitleaks) | `secret_scan.py` | weekly | `secret_exposure` |
| 3 | Cloudflare posture | `cf_posture.py` | weekly, token-gated | `config_drift` |

All three obey Heimdall's security invariants (`contracts/src/security.ts`): findings always alert
on first occurrence, and are **never auto-closed** by the scanner — closing a security incident is
always a human action, regardless of how Constitution v2/v3 evolves the fix-autonomy rules for
*other* invariants (see heimdall issue #24 comment thread / issue #31).

## Deployment

- Code lives here (`openclaw` repo, git-tracked); the live copy is `/root/security-scanner/` on
  `openclaw-vps` (same pattern as `vps/model-researcher/` — no deploy script, `scp` changed files
  over and the next cron tick picks them up).
- Secrets: `/root/security-scanner/.env` (chmod 600, never committed):
  ```
  HEIMDALL_SERVICE_KEY=...      # same telemetry key the SDK/self-test use
  CLOUDFLARE_API_TOKEN=...      # optional — cf_posture module skips silently if absent
  CLOUDFLARE_ACCOUNT_ID=...     # optional — falls back to posture-baseline.json's `account` field
  ```
  `HEIMDALL_SERVICE_KEY` resolution order: `$HEIMDALL_SERVICE_KEY` env → this `.env` →
  `/root/.hermes/.env` (same fallback chain as `heimdall-selftest/scripts/self-test.sh`).
- Binary: `bin/gitleaks` is downloaded automatically on first `secret_scan.py` run (not committed —
  see `.gitignore`).
- Cron (system crontab, weekly, Monday 07:00 UTC — after the model-researcher's 06:00 slot):
  ```
  0 7 * * 1 cd /root/security-scanner && /usr/bin/python3 run_scan.py >> /root/security-scanner/scan.log 2>&1
  ```

## Activating cf-posture (Eric's action item)

The Cloudflare posture module is fully built but **skips silently** until a token exists at
`/root/security-scanner/.env`. Mint a read-only API token (Cloudflare dashboard → My Profile → API
Tokens → Create Token → Custom) with exactly:

- **Account** → Workers Scripts → **Read**
- **Account** → Cloudflare Pages → **Read**
- **Account** → Access: Apps and Policies → **Read**
- **User** → API Tokens → **Read**

Scope it to the `eric@thebakkens.net` account only. Once dropped into `.env` as
`CLOUDFLARE_API_TOKEN`, the next scheduled run picks it up automatically — no code change needed.

## First-run seeding

The very first time `run_scan.py` runs (no `state/first_run.flag` yet), if the combined scan across
all three modules turns up more than 5 new findings, only the **5 most severe** page — the rest are
seeded into state as already-known (so they never post later either, silently). This prevents a
first-run flood (e.g. this repo's own 7 open Dependabot alerts) from paging 20 incidents at once.
After the flag is written, every subsequent run pages every genuinely new finding immediately.

To re-trigger first-run behavior (e.g. after a state wipe), delete `state/first_run.flag`.

## Tests

`python3 -m unittest discover tests` — stdlib `unittest` + `unittest.mock`, no network, covers the
diff/fingerprint/first-run-seeding logic in `common.py` and `cf_posture.py`.

## Files

- `common.py` — state I/O, secret resolution, hub ingest, first-run seeding.
- `config.py` + `scanner-config.yaml` — the repo list (a tiny hand-rolled parser, NOT general YAML —
  see the file's docstring for why: stdlib-only constraint, no PyYAML).
- `dependency_scan.py` — Dependabot alerts (preferred) + npm audit fallback.
- `secret_scan.py` + `gitleaks-allowlist.toml` — gitleaks over shallow clones.
- `cf_posture.py` + `posture-baseline.json` + `apps-registry.json` — Cloudflare account diff.
- `run_scan.py` — orchestrates all three, applies first-run seeding, posts to the hub.
