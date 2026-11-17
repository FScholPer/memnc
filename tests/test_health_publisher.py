"""T3: model_manager publishes live NodeHealth on the mesh bus.

Hermetic: the bus is a stub that records publishes; the interval loop is
exercised with a real asyncio loop and a stop event.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from memnc.config import Settings
from memnc.model_manager import HealthPublisher, ModelManager
from memnc.schema import SUBJECT_HEALTH


class _RecordingBus:
    def __init__(self):
        self.published: list[tuple[str, bytes]] = []

    async def publish_envelope(self, subject: str, envelope) -> None:
        self.published.append((subject, envelope.model_dump_json().encode()))


@pytest.fixture
def settings():
    return Settings(node_name="node-a", health_interval_s=0)


def test_health_subject_includes_node():
    assert SUBJECT_HEALTH.format(node="node-a") == "mesh.node.health.node-a"


async def test_publisher_publishes_nodehealth_json(monkeypatch):
    monkeypatch.setattr(
        "memnc.model_manager.probe_gpus", lambda: []
    )
    monkeypatch.setattr(
        "memnc.model_manager.probe_vram",
        lambda: (1_500_000_000, 16_000_000_000),
    )
    monkeypatch.setattr(
        "memnc.model_manager.fetch_loaded_models", lambda base_url: []
    )
    bus = _RecordingBus()
    pub = HealthPublisher(
        manager=ModelManager(node="node-a", base_url="http://x"),
        bus=bus,
        interval_s=0,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(pub.run(stop))
    for _ in range(50):
        if bus.published:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert bus.published, "no health message published"
    subject, payload = bus.published[0]
    assert subject == "mesh.node.health.node-a"
    doc = json.loads(payload)
    assert doc["kind"] == "health"
    assert doc["node"] == "node-a"
    assert doc["payload"]["node"] == "node-a"
    assert doc["payload"]["gpu"]["vram_total_mb"] == pytest.approx(
        16_000_000_000 / 2**20, rel=1e-3
    )


async def test_publisher_survives_collect_failure(monkeypatch):
    def boom():
        raise RuntimeError("sysfs gone")

    monkeypatch.setattr("memnc.model_manager.probe_gpus", boom)
    monkeypatch.setattr("memnc.model_manager.probe_vram", boom)
    monkeypatch.setattr(
        "memnc.model_manager.fetch_loaded_models", lambda base_url: []
    )
    bus = _RecordingBus()
    pub = HealthPublisher(
        manager=ModelManager(node="node-a", base_url="http://x"),
        bus=bus,
        interval_s=0,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(pub.run(stop))
    for _ in range(50):
        if bus.published:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    # Health with zeroed GPU numbers is still published — never crash.
    doc = json.loads(bus.published[0][1])
    assert doc["payload"]["gpu"]["vram_total_mb"] == 0.0


async def test_publisher_stops_cleanly(monkeypatch):
    monkeypatch.setattr(
        "memnc.model_manager.probe_gpus", lambda: []
    )
    monkeypatch.setattr(
        "memnc.model_manager.probe_vram", lambda: (0, 1)
    )
    monkeypatch.setattr(
        "memnc.model_manager.fetch_loaded_models", lambda base_url: []
    )
    bus = _RecordingBus()
    pub = HealthPublisher(
        manager=ModelManager(node="node-a", base_url="http://x"),
        bus=bus,
        interval_s=5,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(pub.run(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
