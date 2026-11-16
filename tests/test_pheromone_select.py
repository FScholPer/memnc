"""Tests for 14.15.10 selection consumption (orchestrator reads the field)."""

import asyncio
from pathlib import Path

from memnc.config import Settings
from memnc.orchestrator import Orchestrator
from memnc.pheromone import PheromoneEntry, edge_key, task_type_from_prompt
from memnc.schema import Role, TaskSnapshot


def _entry(success: int, fail: int = 0, cost_tokens: int = 1000) -> PheromoneEntry:
    return PheromoneEntry(successes=float(success), failures=float(fail), cost_tokens=cost_tokens)


TT = task_type_from_prompt("write a dijkstra script")


def _field(edges: dict[str, PheromoneEntry]) -> dict[str, PheromoneEntry]:
    return {edge_key(TT, edge): e for edge, e in edges.items()}


class _FieldBus:
    def __init__(self, entries):
        self._entries = entries

    async def pheromone_entries(self):
        return self._entries


def _orch(tmp_path: Path, entries, **settings):
    orch = Orchestrator.__new__(Orchestrator)
    orch.settings = Settings(workspace_root=str(tmp_path), **settings)
    orch.bus = _FieldBus(entries)
    return orch


def _snap():
    return TaskSnapshot(task_id="t-sel", trace_id="tr", task="write a dijkstra script")


class TestSelectStrategy:
    def test_empty_field_defaults_to_chain_head(self, tmp_path: Path):
        orch = _orch(tmp_path, {})
        snap = _snap()
        asyncio.run(orch._select_strategy(snap))
        assert snap.routing_edge == "A"
        assert snap.history[-1].event == "route.strategy"

    def test_pheromone_can_raise_above_floor(self, tmp_path: Path):
        orch = _orch(tmp_path, _field({"B": _entry(9, 1)}))
        snap = _snap()
        asyncio.run(orch._select_strategy(snap))
        assert snap.routing_edge == "B"

    def test_weak_planner_discounts_mesh_edge(self, tmp_path: Path):
        entries = _field({"B": _entry(5, 0, cost_tokens=100), "C": _entry(50, 0, cost_tokens=50000)})
        orch = _orch(tmp_path, entries)
        snap = _snap()
        asyncio.run(orch._select_strategy(snap))
        # C has a far higher raw score but pays the planner-capacity penalty.
        assert snap.routing_edge == "B"

    def test_strong_planner_allows_mesh_edge(self, tmp_path: Path):
        entries = _field({"B": _entry(5, 0), "C": _entry(50, 0)})
        orch = _orch(tmp_path, entries, pheromone_weak_planner=False)
        snap = _snap()
        asyncio.run(orch._select_strategy(snap))
        assert snap.routing_edge == "C"

    def test_failed_edge_not_repicked(self, tmp_path: Path):
        entries = _field({"B": _entry(9, 1)})
        orch = _orch(tmp_path, entries)
        snap = _snap()
        snap.routing_edge = "B"
        asyncio.run(orch._select_strategy(snap, failed_edge="B"))
        # B failed: the escalation floor moves to C, pheromone can only pick
        # at or above it - never re-pick the failed edge, never go below.
        assert snap.routing_edge == "C"

    def test_same_edge_no_duplicate_event(self, tmp_path: Path):
        orch = _orch(tmp_path, {})
        snap = _snap()
        snap.routing_edge = "A"
        n = len(snap.history)
        asyncio.run(orch._select_strategy(snap))
        assert len(snap.history) == n

    def test_disabled_flag_is_noop(self, tmp_path: Path):
        orch = _orch(tmp_path, _field({"B": _entry(9, 1)}), pheromone_select=False)
        snap = _snap()
        asyncio.run(orch._select_strategy(snap))
        assert snap.routing_edge == ""

    def test_bus_failure_is_swallowed(self, tmp_path: Path):
        class _BrokenBus:
            async def pheromone_entries(self):
                raise RuntimeError("bucket gone")

        orch = Orchestrator.__new__(Orchestrator)
        orch.settings = Settings(workspace_root=str(tmp_path))
        orch.bus = _BrokenBus()
        snap = _snap()
        asyncio.run(orch._select_strategy(snap))
        assert snap.routing_edge == ""


class TestDispatchPayload:
    def test_strategy_rides_the_dispatch_payload(self, tmp_path: Path):
        from memnc.schema import SUBJECT_CMD

        orch = Orchestrator.__new__(Orchestrator)
        orch.settings = Settings(workspace_root=str(tmp_path))
        captured = {}

        class _Bus:
            async def put_snapshot(self, snapshot):
                pass

            async def publish_envelope(self, subject, envelope):
                captured["subject"] = subject
                captured["payload"] = envelope.payload

            async def emit_event(self, snapshot, event, detail):
                pass

        orch.bus = _Bus()
        snap = _snap()
        snap.routing_edge = "B"
        asyncio.run(orch._dispatch(snap, Role.PLANNER))
        assert captured["subject"] == SUBJECT_CMD.format(role="planner")
        assert captured["payload"]["strategy"] == "B"


class TestDepositEdge:
    def test_deposit_uses_selected_edge(self, tmp_path: Path):
        orch = Orchestrator.__new__(Orchestrator)
        orch.settings = Settings(workspace_root=str(tmp_path))
        deposits = []

        class _Bus:
            async def deposit_pheromone(self, task_type, edge, outcome, tokens=0):
                deposits.append((task_type, edge, outcome))

        orch.bus = _Bus()
        snap = _snap()
        snap.routing_edge = "B"
        snap.state = snap.state  # keep default; DONE check below
        from memnc.schema import TaskState

        snap.state = TaskState.DONE
        asyncio.run(orch._deposit_pheromone(snap))
        assert deposits == [(TT, "B", "success")]
