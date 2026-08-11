#!/usr/bin/env bash
# Regenerate the redacted Hermes config snapshot in this directory from the live
# files on the VPS (heimdall issue #9). Pulls the raw configs over ssh, redacts
# secrets locally with redact.py, and writes the result over the files already
# checked into this directory. Nothing unredacted ever touches disk outside a
# throwaway tmp dir.
#
# Usage: vps/hermes/snapshot.sh [host]   (host defaults to the "openclaw" ssh alias, root@VPS)
#
# After running: `git diff` the result and eyeball it — redact.py targets the
# secret shapes seen on this VPS (see README.md's redaction convention), it is
# not exhaustive secret detection. If anything that looks like a live
# credential survives, add a pattern/key to redact.py before committing.
set -euo pipefail

HOST="${1:-openclaw}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "==> fetching live configs from $HOST"
scp -q "$HOST:/root/.hermes/config.yaml" "$TMP_DIR/config.yaml"
scp -q "$HOST:/root/.hermes/cron/jobs.json" "$TMP_DIR/jobs.json"
scp -q "$HOST:/root/.hermes/webhook_subscriptions.json" "$TMP_DIR/webhook_subscriptions.json"
scp -q "$HOST:/root/.hermes/profiles/heimdall/config.yaml" "$TMP_DIR/heimdall-config.yaml"
scp -q "$HOST:/root/.hermes/profiles/heimdall/SOUL.md" "$TMP_DIR/heimdall-SOUL.md"
scp -q "$HOST:/root/.hermes/profiles/heimdall/profile.yaml" "$TMP_DIR/heimdall-profile.yaml"

echo "==> redacting"
python3 "$SELF_DIR/redact.py" yaml "$TMP_DIR/config.yaml" > "$SELF_DIR/config.yaml"
python3 "$SELF_DIR/redact.py" json-jobs "$TMP_DIR/jobs.json" > "$SELF_DIR/cron/jobs.json"
python3 "$SELF_DIR/redact.py" json-webhooks "$TMP_DIR/webhook_subscriptions.json" > "$SELF_DIR/webhook_subscriptions.json"
python3 "$SELF_DIR/redact.py" yaml "$TMP_DIR/heimdall-config.yaml" > "$SELF_DIR/profiles/heimdall/config.yaml"
cp "$TMP_DIR/heimdall-SOUL.md" "$SELF_DIR/profiles/heimdall/SOUL.md"
cp "$TMP_DIR/heimdall-profile.yaml" "$SELF_DIR/profiles/heimdall/profile.yaml"

echo "==> secret scan (should print nothing)"
grep -nE '[A-Za-z0-9_\-]{20,}' "$SELF_DIR"/config.yaml "$SELF_DIR"/cron/jobs.json "$SELF_DIR"/webhook_subscriptions.json "$SELF_DIR"/profiles/heimdall/config.yaml \
  | grep -viE '<secret:|^\s*#' \
  | grep -E 'key|token|secret|password|bearer|credential' || echo "  (clean)"

echo "==> done. Review with: git -C $SELF_DIR diff"
