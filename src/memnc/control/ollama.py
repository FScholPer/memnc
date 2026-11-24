"""Ollama adapter: the daemon's own API is the source of truth for models.

Lifecycle differs per host (native systemd unit vs. compose container), so the
adapter delegates start/stop/restart/logs to a control adapter that knows the
process side (usually llauncher), and only falls back to ``systemctl --user``
when nothing else is registered. Reading model state always goes through the
Ollama HTTP API — no ``ollama`` binary required.
"""

from __future__ import annotations

import subprocess

import httpx

from .base import (
    HEALTH_DOWN,
    HEALTH_OK,
    HEALTH_OPEN,
    RUNNING,
    UNKNOWN,
    ActionResult,
    BackendControl,
    Service,
)


class OllamaControl:
    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        unit: str = "ollama.service",
        delegate: BackendControl | None = None,
        delegates: list[BackendControl] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.unit = unit
        # Process control lives in the adapter that owns the service (llauncher,
        # a compose container, …). The first one that can see the endpoint wins.
        self.delegates: list[BackendControl] = list(delegates or [])
        if delegate is not None:
            self.delegates.insert(0, delegate)

    def _delegate_target(self) -> tuple[BackendControl, Service] | None:
        for delegate in self.delegates:
            try:
                peer = next(
                    (
                        svc
                        for svc in delegate.services()
                        if svc.endpoint and _same_host_port(svc.endpoint, self.base_url)
                    ),
                    None,
                )
            except Exception:  # noqa: BLE001 — a broken adapter must not break this one
                continue
            if peer is not None:
                return delegate, peer
        return None

    # ── reading ────────────────────────────────────────────────────────────

    def _get(self, path: str, timeout: float = 2.0) -> dict | None:
        try:
            resp = httpx.get(f"{self.base_url}{path}", timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception:  # noqa: BLE001 — a dead daemon is a normal state
            return None

    def services(self) -> list[Service]:
        tags = self._get("/api/tags")
        if tags is None:
            return [
                Service(
                    backend=self.name,
                    name="ollama",
                    state=UNKNOWN,
                    health=HEALTH_DOWN,
                    endpoint=self.base_url,
                    service=self.unit,
                )
            ]

        models = sorted(m.get("name", "") for m in tags.get("models", []) if m.get("name"))
        running = self._get("/api/ps") or {}
        loaded = [m.get("name", "") for m in running.get("models", []) if m.get("name")]

        # When a process adapter owns the same endpoint, let it report the unit
        # state; this row only contributes the model inventory.
        state, service = RUNNING, self.unit
        target = self._delegate_target()
        if target is not None:
            state, service = target[1].state, target[1].service

        return [
            Service(
                backend=self.name,
                name="ollama",
                state=state,
                health=HEALTH_OK if models else HEALTH_OPEN,
                endpoint=self.base_url,
                models=loaded or models,
                service=service,
                detail=f"{len(models)} models" + (f", {len(loaded)} loaded" if loaded else ""),
            )
        ]

    # ── control ────────────────────────────────────────────────────────────

    def _delegate_or_systemctl(self, verb: str) -> ActionResult:
        target = self._delegate_target()
        if target is not None:
            adapter, svc = target
            return getattr(adapter, verb)(svc.name)

        try:
            proc = subprocess.run(
                ["systemctl", "--user", verb, self.unit],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ActionResult(False, verb, self.name, "ollama", str(exc))
        message = (proc.stderr or proc.stdout).strip()
        if proc.returncode != 0 and "not found" in message.lower():
            message = (
                f"{self.unit} does not exist on this host — register a process "
                f"adapter (llauncher) that owns the ollama service"
            )
        return ActionResult(
            ok=proc.returncode == 0,
            action=verb,
            backend=self.name,
            name="ollama",
            message=message,
        )

    def start(self, name: str) -> ActionResult:
        return self._delegate_or_systemctl("start")

    def stop(self, name: str) -> ActionResult:
        return self._delegate_or_systemctl("stop")

    def restart(self, name: str) -> ActionResult:
        return self._delegate_or_systemctl("restart")

    def logs(self, name: str, lines: int = 100) -> str:
        target = self._delegate_target()
        if target is not None:
            adapter, svc = target
            try:
                return adapter.logs(svc.name, lines)
            except Exception as exc:  # noqa: BLE001
                return f"no logs via {adapter.name}: {exc}"
        try:
            proc = subprocess.run(
                ["journalctl", "--user", "-u", self.unit, "-n", str(lines), "--no-pager"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return proc.stdout or proc.stderr
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"no logs: {exc}"


def _same_host_port(endpoint: str, base_url: str) -> bool:
    """Compare '127.0.0.1:11434' style endpoints without a URL parser fight."""
    from urllib.parse import urlparse

    a = endpoint.strip()
    b = base_url.strip()
    if "://" not in a:
        a = "http://" + a
    pa, pb = urlparse(a), urlparse(b)
    return (pa.hostname, pa.port) == (pb.hostname, pb.port)


assert isinstance(OllamaControl(delegate=None), BackendControl)
