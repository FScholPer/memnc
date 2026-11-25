# hermes-llauncher

Hermes plugin pair that makes **lLauncher the single model catalog** for Hermes:
every model lLauncher runs becomes a Hermes provider (and, with a small core patch,
a row in the `/model` picker). No `custom_providers` blocks, no model lists
duplicated in `config.yaml`, no Hermes restart when you start/stop a model in
lLauncher.

```
lLauncher :8765 ──GET /models──► provider/  (kind: model-provider) ──► providers "llauncher-<model>"
                             └──► catalog/   (kind: standalone)    ──► /model picker rows
```

Two directories, because Hermes loads them at different times for different jobs:

| dir | plugin kind | loaded by | job |
| --- | --- | --- | --- |
| `provider/` | `model-provider` | provider discovery, before any request | register `llauncher-<model>` → `http://127.0.0.1:<port>/v1` |
| `catalog/` | `standalone` | plugin manager (gateway start) | `provider_rows` hook → rows in the model switch |

The ~40 lines of catalog/token plumbing are duplicated on purpose: the two
plugins are imported as separate top-level modules, so a shared file would mean
`snippets in sys.path` hacks instead of a copy.

## Install

```bash
./install.sh                 # symlinks both dirs into ~/.hermes/plugins
./install.sh --uninstall     # removes both links
```

Then, if Hermes lacks the picker hook (older versions):

```bash
cd ~/.hermes/hermes-agent && git apply ~/memnc/plugins/hermes-llauncher/patches/provider-rows-hook.patch
```

The patch adds `provider_rows` to `VALID_HOOKS` (`hermes_cli/plugins.py`) and
merges hook rows into the picker build
(`hermes_cli/model_switch_providers.py` :: `_lap_plugin_rows`). Without it the
plugin still registers providers — `hermes --provider llauncher-swift --model
swift-1.5` works — but the interactive picker will not list them.

Restart the gateway after installing so the plugins are imported.

## Config

All optional, via environment:

| variable | default | meaning |
| --- | --- | --- |
| `LLAUNCHER_API_URL` | `http://127.0.0.1:8765` | lLauncher REST base |
| `LLAUNCHER_AGENT_TOKEN` | — | API key for the REST call |
| `LLAUNCHER_ENV_FILE` | `~/.llauncher/agent.env` | fallback for the token |

## Verify

```bash
cd ~/.hermes/hermes-agent && ./venv/bin/python -c "
from providers import get_provider_profile, list_providers
print([p.name for p in list_providers() if p.name.startswith('llauncher')])
print(get_provider_profile('llauncher-swift15').base_url)"
```

Provider side works in a bare interpreter; the catalog side needs the plugin
manager (gateway) to call `register()` — check `/model` or `hermes plugins list`.

Expected: one `llauncher-<slug>` per running lLauncher model, base_url on the
model's `running_port`, and matching picker rows with `"source": "llauncher"`.

## Notes

- Slug: `llauncher-` + `name` from lLauncher, lowercased, non-alphanumerics → `-`
  (`Swift 1.5` → `llauncher-swift-15`).
- Model id: asked from each server's `/v1/models` (the llama-server alias), so
  give servers `--alias`. Otherwise the id is the full `.gguf` path — valid, ugly.
- Only running models appear. A stopped model has no port; a row that cannot
  serve a request is worse than a missing row.
- lLauncher down/unauth/timeout → no rows, no profiles, no traceback.
- `llauncher-` is reserved by this plugin.
