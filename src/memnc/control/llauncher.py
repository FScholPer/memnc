"""llauncher adapter: treats the llauncher registry as the source of truth.

llauncher already knows how to manage systemd user units and compose services,
so this adapter does not re-implement process control — it shells out to the
``llauncher`` CLI and normalises its JSON snapshot onto ``memnc.control.base``.
That keeps one implementation of "how do I stop a unit" in the stack.

Requires ``llauncher`` on PATH (or ``LLAUNCHER_BIN``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from .base import (
    CLOUD,
    FAILED,
    HEALTH_BAD,
    HEALTH_CLOUD,
    HEALTH_DOWN,
    HEALTH_OK,
    HEALTH_OPEN,
    RUNNING,
    STOPPED,
    UNKNOWN,
    ActionResult,
    BackendControl,
    BackendError,
    Service,
)

STATE_MAP = {
    "active": RUNNING,
    "running": RUNNING,
    "inactive": STOPPED,
    "failed": FAILED,
    "activating": RUNNING,
    "deactivating": RUNNING,
    "no-service": CLOUD,
}


class LlauncherControl:
    name = "llauncher"

    def __init__(self, binary: str = "llauncher", timeout: float = 20.0) -> None:
        self.binary = binary
        self.timeout = timeout

    # ── plumbing ───────────────────────────────────────────────────────────

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _run(self, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
        if not self.available():
            raise BackendError(f"{self.binary!r} not on PATH")
        env = dict(os.environ)
        # systemctl --user needs a runtime dir even when called from cron/agents.
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        try:
            return subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendError(f"{self.binary} {' '.join(args)} timed out") from exc

    def _json(self, *args: str) -> object:
        proc = self._run(*args)
        if proc.returncode != 0:
            raise BackendError((proc.stderr or proc.stdout).strip() or "llauncher failed")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise BackendError(f"unparsable llauncher output: {proc.stdout[:200]!r}") from exc

    # ── reading ────────────────────────────────────────────────────────────

    def services(self) -> list[Service]:
        if not self.available():
            return []
        try:
            snaps = self._json("backend", "list", "--json")
        except BackendError:
            return []
        if not isinstance(snaps, list):
            return []
        return [self._to_service(s) for s in snaps if isinstance(s, dict)]

    @staticmethod
    def _to_service(snap: dict) -> Service:
        unit = snap.get("unit") or {}
        native = str(unit.get("active_state") or "")
        state = STATE_MAP.get(native, UNKNOWN if snap.get("manages_service") else CLOUD)

        port_open = snap.get("port_open")
        health = snap.get("health") or {}
        if port_open is None:
            health_word = HEALTH_CLOUD if not snap.get("manages_service") else UNKNOWN
        elif not port_open:
            health_word = HEALTH_DOWN
        elif "ok" in health:
            health_word = HEALTH_OK if health.get("ok") else HEALTH_BAD
        else:
            health_word = HEALTH_OPEN

        models = [snap["model"].rsplit("/", 1)[-1]] if snap.get("model") else []
        return Service(
            backend="llauncher",
            name=str(snap.get("name") or "?"),
            state=state,
            health=health_word,
            endpoint=str(snap.get("endpoint") or ""),
            models=models,
            service=str(snap.get("service") or unit.get("compose_service") or ""),
            detail=str(snap.get("provider") or ""),
        )

    # ── control ────────────────────────────────────────────────────────────

    def _verb(self, verb: str, name: str) -> ActionResult:
        proc = self._run("backend", verb, name, timeout=180)
        out = (proc.stdout or proc.stderr).strip()
        return ActionResult(
            ok=proc.returncode == 0,
            action=verb,
            backend=self.name,
            name=name,
            message=out.splitlines()[-1] if out else "",
        )

    def start(self, name: str) -> ActionResult:
        return self._verb("start", name)

    def stop(self, name: str) -> ActionResult:
        return self._verb("stop", name)

    def restart(self, name: str) -> ActionResult:
        return self._verb("restart", name)

    def logs(self, name: str, lines: int = 100) -> str:
        proc = self._run("backend", "logs", name, "-n", str(lines), timeout=60)
        if proc.returncode != 0 and not proc.stdout.strip():
            raise BackendError((proc.stderr or "no logs").strip())
        return proc.stdout


# Structural check at import time: an adapter that drifts from the contract
# fails loudly here rather than at first use in the orchestrator.
assert isinstance(LlauncherControl(), BackendControl)
