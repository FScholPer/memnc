#!/usr/bin/env bash
# Dreaming job launcher (ExecStart for the systemd user timer, 14.13 Step 6).
# Runs one nightly consolidation pass: `python -m memnc.dreaming --apply`.
#
# Rules encoded here (same as model-manager.sh / backlog-driver.sh):
# - never `source` .env (unquoted values with spaces abort the shell);
#   extract single vars with the plain-match awk pattern instead.
# - host-side clients need a loopback URL; the .env NATS_URL carries the
#   compose-internal hostname (`nats`), which does not resolve on the host.
# - never export an empty NATS_TOKEN (Authorization Violation looks like
#   wrong credentials); only export when non-empty.
# - strip PYTHONPATH: a leaked value from another env shadows the venv's
#   site-packages and produces misleading import errors.
# - the job is dormant by design: it refuses to run unless
#   DREAMING_ENABLED=1 is set by the operator (the human sign-off step).
#   A disabled run exits 0 cleanly (logged, no error), so the timer is safe
#   to enable before the operator flips the flag.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"

nats_token="$(awk -F= -v k=NATS_TOKEN '$1 == k {sub(/^[^=]*=/,""); gsub(/^"|"$/,""); print; exit}' "$REPO/.env")"
export NATS_URL="${NATS_URL:-nats://127.0.0.1:4222}"
if [ -n "$nats_token" ]; then
  export NATS_TOKEN="$nats_token"
fi
# NKey seed: the node's transport identity. Default: this host's gpu
# identity; override per node with MEMNC_NKEYS_SEED.
export NATS_NKEYS_SEED="${MEMNC_NKEYS_SEED:-$REPO/secrets/$(hostname)-gpu.nk}"
export NODE_NAME="${MEMNC_NODE_NAME:-$(hostname)-gpu}"
# Operator sign-off: enable the job by exporting DREAMING_ENABLED=1 (the
# systemd unit may set Environment=DREAMING_ENABLED=1 once approved).
export DREAMING_ENABLED="${DREAMING_ENABLED:-${MEMNC_DREAMING_ENABLED:-0}}"
# Experience store path (the job merges bullets here on --apply).
export EXPERIENCE_STORE_PATH="${EXPERIENCE_STORE_PATH:-$REPO/workspace/experience-store.json}"

exec env -u PYTHONPATH "$REPO/.venv/bin/python" -m memnc.dreaming --apply
