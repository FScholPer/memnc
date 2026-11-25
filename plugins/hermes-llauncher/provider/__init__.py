"""llauncher model-provider: every running lLauncher model as a Hermes provider.

One central catalog (lLauncher REST API) instead of hand-maintained
``custom_providers`` entries. Each running model becomes its own provider
``llauncher-<model>`` pointing at ``http://127.0.0.1:<running_port>/v1``.

Loaded by Hermes' provider discovery (``kind: model-provider``), so the
profiles exist before config/provider resolution — CLI, cron and subagents
included, not just the interactive picker.

Source of truth: GET /models on lLauncher (``X-Api-Key``).
Token: $LLAUNCHER_AGENT_TOKEN, else LLAUNCHER_AGENT_TOKEN=... in
$LLAUNCHER_ENV_FILE (default ~/.llauncher/agent.env).

Stopped models are deliberately not registered: no port, no row.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from pathlib import Path

from providers import register_provider
from providers.base import ProviderProfile

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
    """Models known to lLauncher; [] when the daemon is unreachable."""
    try:
        data = _json(f"{LAUNCHER_API.rstrip('/')}/models", key=("X-Api-Key", _token()))
    except Exception as exc:  # down, auth, timeout — never break Hermes startup
        log.debug("llauncher catalog unavailable: %s", exc)
        return []
    return data if isinstance(data, list) else []


def served_ids(port: int) -> tuple[str, ...]:
    """Model ids the server on *port* advertises (the -a/--alias when set)."""
    try:
        data = _json(f"http://127.0.0.1:{port}/v1/models")
    except Exception:
        return ()
    items = data.get("data") if isinstance(data, dict) else data
    return tuple(str(m["id"]) for m in (items or []) if isinstance(m, dict) and m.get("id"))


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "model"


def _profile(entry: dict) -> ProviderProfile | None:
    name = str(entry.get("name") or "").strip()
    port = entry.get("running_port")
    if not name or not port:
        return None
    ids = served_ids(int(port)) or (name,)
    return ProviderProfile(
        name=f"llauncher-{slug(name)}",
        display_name=f"lLauncher · {name}",
        description=f"Local lLauncher model {name} (:{port}, ctx {entry.get('ctx_size') or '?'})",
        base_url=f"http://127.0.0.1:{int(port)}/v1",
        static_models=ids,
        fallback_models=ids,
        supports_model_listing=False,
        supports_health_check=False,
        auth_type="none",
        env_vars=(),
    )


def register_all() -> list[str]:
    names = []
    for entry in catalog():
        if not entry.get("running"):
            continue
        profile = _profile(entry)
        if profile is None:
            continue
        try:
            register_provider(profile)
        except Exception as exc:  # duplicate after a refresh, malformed entry
            log.debug("llauncher: cannot register %s: %s", entry.get("name"), exc)
            continue
        names.append(profile.name)
    return names


_registered = register_all()
if _registered:
    log.debug("llauncher providers registered: %s", ", ".join(_registered))
