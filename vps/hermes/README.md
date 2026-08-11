# Hermes config snapshot (heimdall issue #9)

Redacted-secrets snapshot of the live Hermes config on **openclaw-vps**
(`/root/.hermes/`). The VPS copy is the one that's actually live — this
directory exists so a disk loss doesn't also lose the config shape (2236
lines of `config.yaml` vs ~200 in the `hermes-chief-of-staff-cloudflare`
repo template) and so config drift is visible in `git diff` instead of only
in 20+ ad-hoc `.bak` files.

## Files

- `config.yaml` — redacted copy of `/root/.hermes/config.yaml` (top-level
  Hermes config: providers, platforms, MCP servers, custom model providers).
- `cron/jobs.json` — redacted copy of `/root/.hermes/cron/jobs.json` (all
  scheduled Hermes agent jobs, including the model-researcher and
  bridge-adjacent jobs).
- `webhook_subscriptions.json` — redacted copy of
  `/root/.hermes/webhook_subscriptions.json` (inbound webhook routes,
  including the `heimdall-incidents`, `heimdall-feedback`, and
  `heimdall-diagnose` subscriptions Heimdall's hub posts to).
- `profiles/heimdall/{config.yaml,profile.yaml,SOUL.md}` — the Heimdall
  Hermes profile's own config (model routing, gateway port, agent
  behavior), provisioning description, and system prompt. This profile had
  no live secrets in it as of this snapshot (checked; scan came back clean).
  **Not snapshotted:** `profiles/heimdall/.env` and
  `profiles/heimdall/access-token.env` — these are pure credential files
  with nothing else in them, intentionally excluded rather than redacted.
- `redact.py` — the redaction logic, called by `snapshot.sh`.
- `snapshot.sh` — regenerates all of the above from the live VPS files.

Only one Hermes profile currently exists that's relevant to Heimdall
(`heimdall`); there is no separate `heimdall-review` profile on the VPS as of
this snapshot.

## Redaction convention

Every secret value is replaced with `<secret:NAME>`, e.g.:

```yaml
token: <secret:TOKEN>
```

```json
"secret": "<secret:webhook_heimdall-incidents>"
```

`redact.py` does this in two passes:

1. **Structured key redaction** — YAML `key: value` and JSON `"key": "value"`
   pairs where the key is one of `token`, `secret`, `password`, `api_key`,
   `session_key`, `client_secret`, `access_token`, `refresh_token`,
   `auth_token`, `webhook_secret` (case-insensitive) and the value is
   non-empty.
2. **Inline pattern redaction** — token shapes that show up pasted into free
   text (cron job prompts routinely paste `Authorization: Bearer <token>`
   examples or literal API tokens), matched by prefix/shape: `Bearer <token>`,
   `ol_api_...` (Outline), `crw_...` (Windshift), `sk-...`, `ll-...`
   (LightLLM), and `<digits>:<token>` (Telegram bot token format).

This is **not** exhaustive secret detection — it targets the patterns
actually found on this VPS. `key_env: SOME_ENV_VAR_NAME` fields are left
alone on purpose; they name an environment variable, they aren't a secret
value themselves.

## Regenerating the snapshot

```
vps/hermes/snapshot.sh [host]   # host defaults to the "openclaw" ssh alias
```

This scp's the live files into a throwaway tmp dir, runs `redact.py` on each,
writes the result over the files in this directory, then re-scans the output
and prints anything that still looks credential-shaped (should print nothing
— review the diff before committing either way). No unredacted file is ever
written outside the tmp dir, which is deleted on exit.

`.bak` files under `/root/.hermes/` (ad-hoc versioning, 20+ at last count)
are superseded by this snapshot + git history — periodically prune old ones
on the VPS directly; this snapshot doesn't manage them.
