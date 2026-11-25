"""llauncher-catalog: lLauncher's catalog as Hermes model-picker rows.

The model switch builds its rows from config (``custom_providers``) or the
built-in local runtime. This plugin feeds the same picker from the ONE central
catalog — lLauncher's REST API — so a model only has to be known to lLauncher,
never duplicated into Hermes config.

Companion: the ``llauncher`` model-provider plugin (``provider/``) registers the
profiles these rows resolve to. This plugin only adds rows; without the core
hook ``provider_rows`` it is inert, never broken.

Config: LLAUNCHER_API_URL, LLAUNCHER_AGENT_TOKEN, LLAUNCHER_ENV_FILE.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

LAUNCHER_API = os.environ.get("LLAUNCHER_API_URL", "http://127.0.0.1:8765")


def _token() -> str:
    tok = os.environ.get("LLAUNCHER_AGENT_TOKEN", "").strip()
    if tok:
        return tok
    path = Path(os.environ.get("LLAUNCHER_ENV_FILE", "~/.llauncher/agent.env")).expanduser()
    try:
        for line in path.read_text().splitlines():
            if line.startswith("LLAUNCHER_AGENT_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _json(url: str, *, key: tuple[str, str] | None = None, timeout: float = 2.0):
    req = urllib.request.Request(url)
    if key:
        req.add_header(*key)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def catalog() -> list[dict]:
    try:
        data = _json(f"{LAUNCHER_API.rstrip('/')}/models", key=("X-Api-Key", _token()))
    except Exception as exc:
        log.debug("llauncher catalog unavailable: %s", exc)
        return []
    return data if isinstance(data, list) else []


def served_ids(port: int) -> list[str]:
    try:
        data = _json(f"http://127.0.0.1:{port}/v1/models")
    except Exception:
        return []
    items = data.get("data") if isinstance(data, dict) else data
    return [str(m["id"]) for m in (items or []) if isinstance(m, dict) and m.get("id")]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "model"


def provider_rows(
    *,
    for_picker: bool = False,
    current_provider: str = "",
    current_base_url: str = "",
    current_model: str = "",
    **_ignored,
) -> list[dict]:
    """One row per running lLauncher model, slug ``llauncher-<model>``.

    Contract: list of dicts with slug/name/models/api_url; the core merges them
    into the picker (hermes_cli/model_switch_providers.py :: _lap_plugin_rows).
    """
    rows: list[dict] = []
    current = str(current_provider or "").strip().lower()
    for entry in catalog():
        if not entry.get("running") or not entry.get("running_port"):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        port = int(entry["running_port"])
        row_slug = f"llauncher-{slug(name)}"
        ids = served_ids(port) or [name]
        rows.append(
            {
                "slug": row_slug,
                "name": f"lLauncher · {name}",
                "is_current": bool(current) and current == row_slug,
                "is_user_defined": True,
                "models": ids,
                "total_models": len(ids),
                "source": "llauncher",
                "api_url": f"http://127.0.0.1:{port}/v1",
                "native_catalog_empty": False,
            }
        )
    return rows


def register(ctx):
    ctx.register_hook("provider_rows", provider_rows)


__all__ = ["catalog", "provider_rows", "register", "served_ids"]
