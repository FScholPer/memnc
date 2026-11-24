"""Backend lifecycle abstraction — memnc owns the contract, adapters implement it.

This layer answers two questions per backend family:

1. *What is running?*            -> ``services()`` / ``get()``
2. *How do I control it?*       -> ``start()`` / ``stop()`` / ``restart()`` / ``logs()``

The point of the indirection: memnc must not care whether a model server is a
systemd user unit, a compose container, an Ollama daemon or a cloud endpoint.
Adapters (``llauncher``, ``ollama``, …) translate the contract; this module has
no I/O of its own and no knowledge of NATS.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol, runtime_checkable

# Normalised state/health vocabulary. Adapters map their native words onto
# these so callers (CLI, orchestrator, resource planner) can switch on them.
RUNNING = "running"
STOPPED = "stopped"
FAILED = "failed"
CLOUD = "cloud"
UNKNOWN = "unknown"

HEALTH_OK = "ok"
HEALTH_DOWN = "down"
HEALTH_OPEN = "open"
HEALTH_BAD = "bad"
HEALTH_CLOUD = "cloud"


@dataclass(slots=True)
class Service:
    """One controllable (or at least addressable) backend instance."""

    backend: str  # adapter that produced this row: 'llauncher' | 'ollama' | ...
    name: str  # adapter-local name, e.g. 'swift', 'ollama'
    state: str = UNKNOWN
    health: str = UNKNOWN
    endpoint: str = ""
    models: list[str] = field(default_factory=list)
    service: str = ""  # systemd unit or compose service, "" if unmanaged
    detail: str = ""

    @property
    def managed(self) -> bool:
        """True when the adapter can start/stop it."""
        return bool(self.service) or self.state not in (CLOUD,)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["managed"] = self.managed
        return d


@dataclass(slots=True)
class ActionResult:
    ok: bool
    action: str
    backend: str
    name: str
    message: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class BackendError(RuntimeError):
    """Adapter could not talk to its backend (binary missing, timeout, ...)."""


def get_service(adapter: BackendControl, name: str) -> Service | None:
    """Look one service up by name on a single adapter."""
    for svc in adapter.services():
        if svc.name == name:
            return svc
    return None


@runtime_checkable
class BackendControl(Protocol):
    """Contract every backend adapter implements."""

    name: str

    def services(self) -> list[Service]:
        """All instances this adapter knows about. Must not raise on a dead backend."""
        ...

    def start(self, name: str) -> ActionResult: ...

    def stop(self, name: str) -> ActionResult: ...

    def restart(self, name: str) -> ActionResult: ...

    def logs(self, name: str, lines: int = 100) -> str:
        """Last ``lines`` log lines, or a short explanation why there are none."""
        ...
