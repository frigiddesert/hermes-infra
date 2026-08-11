#!/usr/bin/env bash
# Back up the model-researcher's diff baseline (state.json) into this repo directory
# (heimdall issue #9 — state.json lived only on VPS disk with no git anywhere; a disk
# loss would silently reset the weekly new-model diff baseline).
#
# Run manually after a `model-researcher-weekly` cron tick, or wire it into the
# existing Hermes weekly job (see README.md) so the copy happens automatically.
#
# Usage: vps/model-researcher/backup-state.sh [host]   (host defaults to the "openclaw" ssh alias)
set -euo pipefail

HOST="${1:-openclaw}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> copying state.json from $HOST"
scp -q "$HOST:/root/workspace/model-researcher/state.json" "$SELF_DIR/state.json"

if git -C "$SELF_DIR" diff --quiet -- state.json 2>/dev/null; then
  echo "==> state.json unchanged, nothing to commit"
else
  echo "==> state.json updated — review with: git -C $SELF_DIR diff -- vps/model-researcher/state.json"
fi
