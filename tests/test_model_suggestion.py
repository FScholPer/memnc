"""Tests for the failure-card model suggestion flow (P2 item 9)."""

import asyncio
from pathlib import Path

from memnc.config import Settings
from memnc.orchestrator import Orchestrator
from memnc.schema import ModelReputation, NodeInfo, TaskSnapshot


def _snapshot(route: dict[str, str] | None = None) -> TaskSnapshot:
    snap = TaskSnapshot(task_id="t-sug-1", trace_id="tr-1", task="task")
    snap.route = route or {"coder": "n1:m-a"}
    return snap


def _orch(tmp_path: Path, nodes: list[NodeInfo], reps: list[ModelReputation]):
    orch = Orchestrator.__new__(Orchestrator)
    orch.settings = Settings(workspace_root=str(tmp_path))
    orch.bus = _Bus(nodes, reps)
    return orch


class _Bus:
    def __init__(self, nodes, reps):
        self._nodes = nodes
        self._reps = reps

    async def list_nodes(self):
        return self._nodes

    async def list_reputation(self):
        return self._reps


def _nodes() -> list[NodeInfo]:
    return [
        NodeInfo(node_id="n1", roles=["coder", "planner"], models=["m-a", "m-b"]),
        NodeInfo(node_id="n2", roles=["coder"], models=["m-a", "m-c"]),
        NodeInfo(node_id="n3", roles=["coder"], models=["m-d"], paused=True),
    ]


class TestSuggestModel:
    def test_suggests_approved_alternative(self, tmp_path: Path):
        orch = _orch(
            tmp_path,
            _nodes(),
            [ModelReputation(model="m-c", approvals=4)],
        )
        snap = _snapshot()
        asyncio.run(orch._suggest_model(snap, "coder", "500 Internal Server Error"))
        entry = snap.history[-1]
        assert entry.event == "model.suggest"
        assert "m-c" in entry.detail

    def test_ranking_prefers_approvals(self, tmp_path: Path):
        orch = _orch(
            tmp_path,
            _nodes(),
            [
                ModelReputation(model="m-c", approvals=4),
                ModelReputation(model="m-b", approvals=9),
            ],
        )
        snap = _snapshot()
        asyncio.run(orch._suggest_model(snap, "coder", "no JSON object found"))
        assert "m-b" in snap.history[-1].detail

    def test_skips_failed_and_paused_nodes(self, tmp_path: Path):
        # n3 is paused; m-a is the failed model. Only m-b (n1) and m-c (n2)
        # are candidates; the pick must not be the failed model.
        orch = _orch(tmp_path, _nodes(), [])
        snap = _snapshot()
        asyncio.run(orch._suggest_model(snap, "coder", "ReadTimeout"))
        assert "m-b" in snap.history[-1].detail

    def test_skips_retired_model(self, tmp_path: Path):
        orch = _orch(
            tmp_path,
            _nodes(),
            [ModelReputation(model="m-c", approvals=99, retired=True)],
        )
        snap = _snapshot()
        asyncio.run(orch._suggest_model(snap, "coder", "ConnectionError"))
        assert "m-c" not in snap.history[-1].detail

    def test_no_alternative_recorded(self, tmp_path: Path):
        from memnc.schema import NodeInfo as N

        orch = _orch(
            tmp_path,
            [N(node_id="n1", roles=["coder"], models=["m-a"])],
            [],
        )
        snap = _snapshot()
        asyncio.run(orch._suggest_model(snap, "coder", "timed out"))
        assert "none" in snap.history[-1].detail

    def test_unknown_failure_never_suggests(self, tmp_path: Path):
        orch = _orch(tmp_path, _nodes(), [])
        snap = _snapshot()
        asyncio.run(orch._suggest_model(snap, "coder", "critic rejected: bad tests"))
        assert snap.history == []

    def test_empty_role_never_suggests(self, tmp_path: Path):
        orch = _orch(tmp_path, _nodes(), [])
        snap = _snapshot()
        asyncio.run(orch._suggest_model(snap, "", "timed out"))
        assert snap.history == []


class TestTelegramCard:
    def test_card_carries_suggestion(self):
        from memnc.telegram import failure_suggestion

        snap = _snapshot()
        snap.record("task.failed", "boom")
        snap.record("model.suggest", "coder: m-b (timeout)")
        assert failure_suggestion(snap) == "next try: coder: m-b (timeout)"

    def test_card_without_suggestion(self):
        from memnc.telegram import failure_suggestion

        snap = _snapshot()
        snap.record("task.failed", "boom")
        assert failure_suggestion(snap) == ""


def test_role_fallback_from_route(tmp_path: Path):
    """No event role: the failing role falls back to the last route entry."""
    orch = _orch(tmp_path, _nodes(), [])
    snap = _snapshot(route={"planner": "n1:m-a", "coder": "n1:m-a"})
    asyncio.run(orch._suggest_model(snap, "coder", "timed out"))
    assert "coder" in snap.history[-1].detail
