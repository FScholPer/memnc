"""15.4.6 VRAM fit gate: worker load-path wiring."""

from __future__ import annotations

from test_m14 import SilentBus
from test_t7 import _Msg, _pull_envelope

import memnc.worker as worker_mod
from memnc.config import Settings
from memnc.schema import Envelope, Kind, ModelRequest, Role
from memnc.worker import Worker


def _load_envelope(model: str) -> Envelope:
    request = ModelRequest(request_id="r3", model=model, action="load", status="approved")
    return Envelope(
        task_id="t",
        trace_id="t",
        kind=Kind.COMMAND,
        role=Role.PLANNER,
        payload={"request": request.model_dump()},
    )


class _FakeProbe:
    """Deterministic stand-in for the VRAM probes."""

    def __init__(self, cards):
        self.cards = cards

    def gpus(self, base_url):
        return self.cards


def _patch_env(monkeypatch, sizes, cards, loaded=None):
    async def fake_sizes(base_url):
        return sizes

    def fake_probe_gpus():
        return cards

    monkeypatch.setattr(worker_mod, "fetch_model_sizes", fake_sizes)
    monkeypatch.setattr("memnc.vram_probe.probe_gpus", fake_probe_gpus)
    if loaded is not None:
        monkeypatch.setattr(
            "memnc.vram_probe.fetch_loaded_models", lambda base_url: loaded
        )


async def test_load_rejected_when_model_fits_no_card(monkeypatch):
    _patch_env(monkeypatch, sizes={"big:32b": 20 * 2**30}, cards=[(15 * 2**30, 16 * 2**30)])
    bus = SilentBus(Settings(role="coder"))
    w = Worker(Settings(role="coder"), bus=bus)
    loads: list[str] = []

    async def fake_load(base_url, model, keep_alive="30m"):
        loads.append(model)
        return True

    monkeypatch.setattr(worker_mod, "load_model", fake_load)
    msg = _Msg(_load_envelope("big:32b"))
    await w._handle_model_decision(msg)
    assert msg.acked
    assert loads == []  # 20 GB model on a 16 GB card: rejected before load


async def test_load_executes_when_model_fits(monkeypatch):
    sizes = {"small:8b": 5 * 2**30}
    _patch_env(monkeypatch, sizes=sizes, cards=[(10 * 2**30, 16 * 2**30)], loaded=[])
    bus = SilentBus(Settings(role="coder"))
    w = Worker(Settings(role="coder"), bus=bus)
    loads: list[str] = []

    async def fake_load(base_url, model, keep_alive="30m"):
        loads.append(model)
        return True

    monkeypatch.setattr(worker_mod, "load_model", fake_load)
    w._disk_models = ['small:8b']
    msg = _Msg(_load_envelope("small:8b"))
    await w._handle_model_decision(msg)
    assert msg.acked
    assert loads == ["small:8b"]


async def test_rest_split_load_is_unloaded(monkeypatch):
    weights = 20 * 2**30
    _patch_env(
        monkeypatch,
        sizes={"split:70b": weights},
        cards=[(8 * 2**30, 32 * 2**30)],
        # only 20% resident -> below the 90% threshold
        loaded=[{"model": "split:70b", "vram_mb": 0.2 * weights / 2**20}],
    )
    bus = SilentBus(Settings(role="coder"))
    w = Worker(Settings(role="coder"), bus=bus)
    loads: list[str] = []
    unloads: list[str] = []

    async def fake_load(base_url, model, keep_alive="30m"):
        loads.append(model)
        return True

    async def fake_unload(base_url, model):
        unloads.append(model)
        return True

    monkeypatch.setattr(worker_mod, "load_model", fake_load)
    monkeypatch.setattr(worker_mod, "unload_model", fake_unload)
    w._disk_models = ['split:70b']
    msg = _Msg(_load_envelope("split:70b"))
    await w._handle_model_decision(msg)
    assert msg.acked
    assert loads == ["split:70b"]
    assert unloads == ["split:70b"]  # degraded load discarded


async def test_gate_off_disables_the_check(monkeypatch):
    _patch_env(monkeypatch, sizes={}, cards=[])
    bus = SilentBus(Settings(role="coder"))
    w = Worker(Settings(role="coder", vram_fit_gate=False), bus=bus)
    loads: list[str] = []

    async def fake_load(base_url, model, keep_alive="30m"):
        loads.append(model)
        return True

    monkeypatch.setattr(worker_mod, "load_model", fake_load)
    w._disk_models = ['mystery:latest']
    msg = _Msg(_load_envelope("mystery:latest"))
    await w._handle_model_decision(msg)
    assert msg.acked
    assert loads == ["mystery:latest"]  # no size data, gate off -> plain load


async def test_pull_path_gates_the_followup_load(monkeypatch):
    _patch_env(monkeypatch, sizes={"big:32b": 20 * 2**30}, cards=[(0, 16 * 2**30)], loaded=[])
    bus = SilentBus(Settings(role="coder"))
    w = Worker(
        Settings(role="coder", mesh_claim=True, model_pull_whitelist=["big*"]),
        bus=bus,
    )
    pulls: list[str] = []
    loads: list[str] = []

    async def fake_pull(base_url, model):
        pulls.append(model)
        return True

    async def fake_load(base_url, model, keep_alive="30m"):
        loads.append(model)
        return True

    monkeypatch.setattr(worker_mod, "pull_model", fake_pull)
    monkeypatch.setattr(worker_mod, "load_model", fake_load)

    async def fake_disk(base_url):
        return ["big:32b"]

    monkeypatch.setattr(worker_mod, "fetch_disk_models", fake_disk)
    msg = _Msg(_pull_envelope("big:32b"))
    await w._handle_model_decision(msg)
    assert msg.acked
    assert pulls == ["big:32b"]
    assert loads == []  # pulled to disk but the VRAM load is refused
