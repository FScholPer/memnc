#!/usr/bin/env bash
# model_manager daemon launcher (ExecStart for the systemd user unit, T8).
# Runs `python -m memnc.model_manager` on one node: health publishing loop
# plus intent server (load/unload/drain/ensure) via the llauncher MCP.
#
# Rules encoded here (same as backlog-driver.sh):
# - never `source` .env (unquoted values with spaces abort the shell);
#   extract single vars with the plain-match awk pattern instead.
# - host-side clients need a loopback URL; the .env NATS_URL carries the
#   compose-internal hostname (`nats`), which does not resolve on the host.
# - never export an empty NATS_TOKEN (Authorization Violation looks like
#   wrong credentials); only export when non-empty.
# - strip PYTHONPATH: a leaked value from another env shadows the venv's
#   site-packages and produces misleading import errors.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/..")"

nats_token="$(awk -F= -v k=NATS_TOKEN '$1 == k {sub(/^[^=]*=/,""); gsub(/^"|"$/,""); print; exit}' "$REPO/.env")"
export NATS_URL="${NATS_URL:-nats://127.0.0.1:4222}"
if [ -n "$nats_token" ]; then
  export NATS_TOKEN="$nats_token"
fi
# NKey seed: the node's transport identity (T8). Default: this host's gpu
# identity; override per node with MEMNC_NKEYS_SEED.
export NATS_NKEYS_SEED="${MEMNC_NKEYS_SEED:-$REPO/secrets/$(hostname)-gpu.nk}"
export NODE_NAME="${MEMNC_NODE_NAME:-$(hostname)}"

exec env -u PYTHONPATH "$REPO/.venv/bin/python" -m memnc.model_manager
