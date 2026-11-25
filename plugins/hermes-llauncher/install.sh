#!/usr/bin/env bash
# Install (or remove) the hermes-llauncher plugins in ~/.hermes/plugins.
#
#   provider/ -> ~/.hermes/plugins/model-providers/hermes-llauncher  (kind: model-provider)
#   catalog/  -> ~/.hermes/plugins/hermes-llauncher                  (kind: standalone)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGINS="${HERMES_PLUGINS_DIR:-$HOME/.hermes/plugins}"
PROVIDER_LINK="$PLUGINS/model-providers/hermes-llauncher"
CATALOG_LINK="$PLUGINS/hermes-llauncher"
CONFIG="${HERMES_CONFIG:-$HOME/.hermes/config.yaml}"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -f "$PROVIDER_LINK" "$CATALOG_LINK"
  echo "removed $PROVIDER_LINK and $CATALOG_LINK"
  exit 0
fi

mkdir -p "$PLUGINS/model-providers"
for link in "$PROVIDER_LINK" "$CATALOG_LINK"; do
  if [[ -e "$link" && ! -L "$link" ]]; then
    echo "refusing: $link exists and is not a symlink" >&2
    exit 1
  fi
done

ln -sfn "$HERE/provider" "$PROVIDER_LINK"
ln -sfn "$HERE/catalog" "$CATALOG_LINK"
echo "linked $PROVIDER_LINK -> $HERE/provider"
echo "linked $CATALOG_LINK  -> $HERE/catalog"

cat <<'EOF'

Next:
  * picker rows need the core hook; if `hermes` lacks it:
      cd ~/.hermes/hermes-agent && git apply <this dir>/patches/provider-rows-hook.patch
  * restart the gateway so the plugin is imported:
      systemctl --user restart hermes-gateway
EOF
