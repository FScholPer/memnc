"""Tests for error-aware retry: failure classification + model rotation (15.4.3)."""

import asyncio
from pathlib import Path

from memnc.failclass import (
    INVALID_OUTPUT,
    MODEL_LEVEL_KINDS,
    SERVER,
    TIMEOUT,
    UNKNOWN,
    classify_failure,
)
from memnc.schema import ResultPayload, Role, TaskSnapshot


def _snapshot(**kw):
    snap = TaskSnapshot(task_id="t-rot-1", trace_id="tr-1", task="task")
    for k, v in kw.items():
        setattr(snap, k, v)
    return snap


class TestClassifyFailure:
    def test_timeout_markers(self):
        assert classify_failure("worker timed out after 180s") == TIMEOUT
        assert classify_failure("ReadTimeout on revision turn") == TIMEOUT

    def test_server_markers(self):
        assert classify_failure("500 Internal Server Error") == SERVER
        assert classify_failure("ConnectionError to inference") == SERVER

    def test_invalid_output_markers(self):
        assert classify_failure("no JSON object found") == INVALID_OUTPUT
        assert classify_failure("1 validation error for CoderOutput") == INVALID_OUTPUT

    def test_timeout_wins(self):
        assert classify_failure("500 after ReadTimeout") == TIMEOUT

    def test_unknown_and_empty(self):
        assert classify_failure("critic rejected: missing tests") == UNKNOWN
        assert classify_failure(None) == UNKNOWN
        assert classify_failure("") == UNKNOWN

    def test_model_level_kinds(self):
        assert MODEL_LEVEL_KINDS == {TIMEOUT, SERVER, INVALID_OUTPUT}


def _orch(tmp_path: Path):
    """Orchestrator with a stub bus exposing a fixed roster."""
    from memnc.config import Settings
    from memnc.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.settings = Settings(workspace_root=str(tmp_path))
    orch.bus = SimpleRosterBus()
    return orch


class SimpleRosterBus:
    def __init__(self, nodes=None):
        from memnc.schema import NodeInfo

        self._nodes = nodes or [
            NodeInfo(node_id="n1", roles=["coder", "planner"], models=["m-a", "m-b"]),
            NodeInfo(node_id="n2", roles=["coder"], models=["m-a", "m-c"]),
            NodeInfo(node_id="n3", roles=["coder"], models=["m-d"], paused=True),
        ]

    async def list_nodes(self):
        return self._nodes


class TestRotateModel:
    def _payload(self, model="m-a", error="500 Internal Server Error", ok=False):
        return ResultPayload(ok=ok, error=error, model=model)

    def test_rotates_to_alternative(self, tmp_path: Path):
        orch = _orch(tmp_path)
        snap = _snapshot(route={"coder": "n1:m-a"})
        asyncio.run(orch._maybe_rotate_model(snap, Role.CODER, self._payload(), "500 Internal Server Error"))
        assert snap.model_overrides["coder"] == "m-b"
        assert any(e.event == "model.rotate" for e in snap.history)

    def test_route_only_fallback_for_failed_model(self, tmp_path: Path):
        orch = _orch(tmp_path)
        snap = _snapshot(route={"coder": "n2:m-c"})
        asyncio.run(
            orch._maybe_rotate_model(
                snap, Role.CODER, ResultPayload(ok=False, error="no JSON object found", model=""), ""
            )
        )
        assert snap.model_overrides["coder"] == "m-a"

    def test_unknown_failure_does_not_rotate(self, tmp_path: Path):
        orch = _orch(tmp_path)
        snap = _snapshot(route={"coder": "n1:m-a"})
        asyncio.run(
            orch._maybe_rotate_model(snap, Role.CODER, self._payload(error="critic says no"), "")
        )
        assert snap.model_overrides == {}

    def test_human_override_wins(self, tmp_path: Path):
        orch = _orch(tmp_path)
        snap = _snapshot(model_override="human-pick", route={"coder": "n1:m-a"})
        asyncio.run(orch._maybe_rotate_model(snap, Role.CODER, self._payload(), ""))
        assert snap.model_overrides == {}

    def test_no_alternative_records_event(self, tmp_path: Path):
        orch = _orch(tmp_path)
        snap = _snapshot(route={"critic": "n3:m-d"})
        asyncio.run(
            orch._maybe_rotate_model(
                snap, Role.CRITIC, ResultPayload(ok=False, error="503", model="m-d"), ""
            )
        )
        assert snap.model_overrides == {}
        assert "no alternative" in snap.history[-1].detail

    def test_no_retry_window_does_nothing(self, tmp_path: Path):
        orch = _orch(tmp_path)
        snap = _snapshot(attempt=3, route={"coder": "n1:m-a"})  # max_attempts=3
        asyncio.run(orch._maybe_rotate_model(snap, Role.CODER, self._payload(), ""))
        assert snap.model_overrides == {}

    def test_paused_node_skipped(self, tmp_path: Path):
        orch = _orch(tmp_path)
        snap = _snapshot(route={"coder": "n3:m-d"})
        asyncio.run(orch._maybe_rotate_model(snap, Role.CODER, self._payload(model="m-d"), ""))
        assert snap.model_overrides["coder"] == "m-a"

    def test_disabled_by_setting(self, tmp_path: Path):
        from memnc.config import Settings
        from memnc.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.settings = Settings(workspace_root=str(tmp_path), model_rotate_on_fail=False)
        orch.bus = SimpleRosterBus()
        snap = _snapshot(route={"coder": "n1:m-a"})
        asyncio.run(orch._maybe_rotate_model(snap, Role.CODER, self._payload(), ""))
        assert snap.model_overrides == {}


class TestDispatchPayload:
    def test_role_override_beats_task_override(self, tmp_path: Path):
        """The rotated coder model must not leak into the next planner hop."""
        from memnc.config import Settings
        from memnc.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.settings = Settings(workspace_root=str(tmp_path))
        orch.bus = _CaptureBus()
        snap = _snapshot(model_override="task-wide", model_overrides={"coder": "m-b"})
        snap.last_action = None
        asyncio.run(orch._dispatch(snap, Role.CODER))
        assert orch.bus.captured["model_override"] == "m-b"
        asyncio.run(orch._dispatch(snap, Role.PLANNER))
        assert orch.bus.captured["model_override"] == "task-wide"


class _CaptureBus:
    def __init__(self):
        self.captured = None

    async def put_snapshot(self, snapshot):
        pass

    async def publish_envelope(self, subject, envelope):
        self.captured = envelope.payload

    async def emit_event(self, snapshot, event, detail):
        pass
