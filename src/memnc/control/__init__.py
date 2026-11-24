"""Adapter registry: build the control adapters configured for this host.

Callers never import a concrete adapter; they ask for ``controls()`` and work
against ``BackendControl``. Adding a backend family = adding a module here.
"""

from __future__ import annotations

from .base import (
    ActionResult,
    BackendControl,
    BackendError,
    Service,
    get_service,
)
from .llauncher import LlauncherControl
from .ollama import OllamaControl

__all__ = [
    "ActionResult",
    "BackendControl",
    "BackendError",
    "Service",
    "collect",
    "controls",
    "find_control",
    "get_service",
]


def controls(settings=None) -> list[BackendControl]:
    """Instantiate adapters for this host.

    Order matters: earlier adapters own process control, later ones may only
    enrich the same row (e.g. ollama contributes its model list to the
    llauncher-managed ollama service instead of creating a duplicate).
    """
    llauncher_bin = getattr(settings, "llauncher_bin", "llauncher")
    llauncher = LlauncherControl(binary=llauncher_bin)
    adapter_list: list[BackendControl] = []
    if llauncher.available():
        adapter_list.append(llauncher)

    adapter_list.append(
        OllamaControl(
            base_url=getattr(settings, "ollama_url", "http://127.0.0.1:11434"),
            unit=getattr(settings, "ollama_unit", "ollama.service"),
            delegate=llauncher if llauncher.available() else None,
        )
    )
    return adapter_list


def collect(
    adapter_list: list[BackendControl] | None = None,
    backend: str = "",
) -> list[Service]:
    """All services, optionally filtered by adapter name, deduped by endpoint.

    ``backend`` filters by the adapter that reported the row, not by service.
    """
    adapters = adapter_list if adapter_list is not None else controls()
    rows: list[Service] = []
    seen: dict[str, Service] = {}
    for adapter in adapters:
        if backend and adapter.name != backend:
            continue
        for svc in adapter.services():
            key = _endpoint_key(svc.endpoint) or f"{svc.backend}:{svc.name}"
            first = seen.get(key)
            if first is None:
                seen[key] = svc
                rows.append(svc)
                continue
            # Merge: keep the process-owning row, union the model inventory.
            for model in svc.models:
                if model not in first.models:
                    first.models.append(model)
            if svc.detail and svc.detail not in first.detail:
                first.detail = f"{first.detail} / {svc.detail}".strip(" /")
    return sorted(rows, key=lambda s: (s.backend, s.name))


def _endpoint_key(endpoint: str) -> str:
    """'http://127.0.0.1:11434' and '127.0.0.1:11434' are the same service."""
    from urllib.parse import urlparse

    raw = (endpoint or "").strip()
    if not raw or not raw[0].isdigit() and "://" not in raw:
        return raw
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    if not parsed.hostname:
        return endpoint.strip()
    return f"{parsed.hostname}:{parsed.port or ''}"


def find_control(
    name: str, adapter_list: list[BackendControl] | None = None
) -> tuple[BackendControl, Service] | None:
    """Locate the adapter that can control ``name`` (first match wins)."""
    for adapter in adapter_list if adapter_list is not None else controls():
        try:
            svc = get_service(adapter, name)
        except Exception:  # noqa: BLE001 — a broken adapter must not hide others
            continue
        if svc is not None:
            return adapter, svc
    return None
