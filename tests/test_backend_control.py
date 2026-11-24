"""Unit tests for memnc.control — the backend lifecycle abstraction.

No live backend and no subprocess: adapters are exercised through their pure
mapping logic plus a fake adapter for registry behaviour. Live end-to-end
checks are the manual smoke test (`meshctl backend list`).
"""

from memnc.control import collect, find_control
from memnc.control.base import CLOUD, HEALTH_OK, RUNNING, Service
from memnc.control.llauncher import LlauncherControl
from memnc.control.ollama import OllamaControl


class FakeAdapter:
    name = "fake"

    def __init__(self, services):
        self._services = services
        self.started: list[str] = []

    def services(self):
        return list(self._services)

    def start(self, name):
        self.started.append(name)
        return None

    def stop(self, name):
        return None

    def restart(self, name):
        return None

    def logs(self, name, lines=100):
        return ""


# -- llauncher snapshot mapping ---------------------------------------------
def test_llauncher_maps_active_unit_and_health():
    svc = LlauncherControl._to_service(
        {
            "name": "swift",
            "service": "swift",
            "manages_service": True,
            "endpoint": "127.0.0.1:8097",
            "port_open": True,
            "health": {"ok": True},
            "model": "/models/Swift-1.5-Qwen.gguf",
            "unit": {"active_state": "active"},
        }
    )
    assert (svc.state, svc.health) == (RUNNING, HEALTH_OK)
    assert svc.models == ["Swift-1.5-Qwen.gguf"]
    assert svc.service == "swift"


def test_llauncher_cloud_backend_is_unmanaged():
    svc = LlauncherControl._to_service(
        {"name": "hf", "manages_service": False, "endpoint": "router.huggingface.co:443"}
    )
    assert svc.state == CLOUD
    assert svc.managed is False


def test_llauncher_dead_port_is_down_not_healthy():
    svc = LlauncherControl._to_service(
        {
            "name": "x",
            "manages_service": True,
            "port_open": False,
            "unit": {"active_state": "failed"},
        }
    )
    assert svc.state == "failed"
    assert svc.health == "down"


# -- registry aggregation ----------------------------------------------------
def test_collect_dedupes_endpoint_and_merges_models():
    owner = FakeAdapter(
        [Service(backend="llauncher", name="ollama", endpoint="127.0.0.1:11434", state=RUNNING)]
    )
    enrich = FakeAdapter(
        [
            Service(
                backend="ollama",
                name="ollama",
                endpoint="http://127.0.0.1:11434",
                models=["a:1b"],
            )
        ]
    )
    rows = collect([owner, enrich])
    assert len(rows) == 1
    assert rows[0].backend == "llauncher"  # process owner keeps the row
    assert rows[0].models == ["a:1b"]


def test_find_control_returns_owning_adapter():
    owner = FakeAdapter([Service(backend="llauncher", name="swift", endpoint=":8097")])
    found = find_control("swift", [owner])
    assert found is not None
    assert found[0] is owner


def test_find_control_unknown_is_none():
    assert find_control("nope", [FakeAdapter([])]) is None


# -- ollama adapter without a daemon -----------------------------------------
def test_ollama_down_is_reported_as_state_not_exception(monkeypatch):
    adapter = OllamaControl()
    monkeypatch.setattr(adapter, "_get", lambda *a, **k: None)
    rows = adapter.services()
    assert len(rows) == 1
    assert rows[0].health == "down"
    assert rows[0].endpoint.endswith(":11434")
