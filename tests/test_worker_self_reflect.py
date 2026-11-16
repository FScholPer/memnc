"""Tests for worker-side strategy honor (14.15.10, arm-B self-reflect edge)."""

import asyncio

from test_m11_claim import _snapshot
from test_m14 import SilentBus

from memnc.config import Settings
from memnc.roles import Role
from memnc.schema import (
    CoderOutput,
    CriticIssue,
    CriticOutput,
    Envelope,
    FileArtifact,
    ResultPayload,
    TaskSnapshot,
    TaskState,
)
from memnc.worker import Worker

CODER_OUT = CoderOutput(
    summary="done",
    files=[FileArtifact(path="notes.txt", content="hi")],
).model_dump()
APPROVED = CriticOutput(approved=True, summary="fine").model_dump()
REJECTED = CriticOutput(
    approved=False,
    summary="wrong",
    issues=[CriticIssue(issue="bad", severity="blocker")],
).model_dump()
QUESTION = CriticOutput(approved=False, summary="", question="which language?").model_dump()


class _Router:
    """Fake _run: canned outputs per role, records (role, model_override)."""

    def __init__(self, coder_outputs, critic_outputs, planner_output=None):
        self.calls: list[tuple[str, str | None]] = []
        self.coder_outputs = list(coder_outputs)
        self.critic_outputs = list(critic_outputs)
        self.planner_output = planner_output

    async def __call__(self, envelope: Envelope, snapshot: TaskSnapshot) -> ResultPayload:
        role = (envelope.role or Role.CODER).value
        self.calls.append((role, (envelope.payload or {}).get("model_override")))
        if role == "coder":
            return ResultPayload(ok=True, output=self.coder_outputs.pop(0), model="m")
        if role == "critic":
            return ResultPayload(ok=True, output=self.critic_outputs.pop(0), model="m")
        if role == "planner":
            assert self.planner_output is not None
            return ResultPayload(ok=True, output=self.planner_output, model="m")
        raise AssertionError(f"unexpected role run: {role}")


def _worker(bus: SilentBus, router: _Router, role: Role) -> Worker:
    settings = Settings(
        role=role.value,
        mesh_claim=True,
        self_pipeline=True,
        apply_artifacts=False,
    )
    w = Worker(settings, bus=bus)
    w._run = router  # type: ignore[method-assign]
    return w


def _edge_snapshot(task_id: str, edge: str, **fields) -> TaskSnapshot:
    return _snapshot(
        task_id,
        state=TaskState.RECEIVED,
        claimable=True,
        next_role="planner",
        routing_edge=edge,
        **fields,
    )


def _claim_and_run(bus, w, task_id, claimant):
    claimed = asyncio.run(bus.try_claim(task_id, claimant))
    assert claimed is not None
    asyncio.run(w._self_pipeline(claimed))
    return asyncio.run(bus.get_snapshot(task_id))


def test_edge_b_skips_planner_and_runs_self_critique_to_done():
    bus = SilentBus(Settings())
    asyncio.run(bus.put_snapshot(_edge_snapshot("t-b1", "B")))
    # A PLANNER worker claims first; edge B must NOT run the planner model —
    # the whole arm-B run (coder + in-process critique) completes in one call.
    router = _Router([CODER_OUT], [APPROVED])
    after = _claim_and_run(bus, _worker(bus, router, Role.PLANNER), "t-b1", "b:planner")
    assert after.state is TaskState.DONE
    assert after.critique is not None and after.critique.approved is True
    assert [r for r, _ in router.calls] == ["coder", "critic"]  # no planner run
    assert any(h.event == "route.self_reflect" for h in after.history)
    assert "critic" not in after.route  # no critic claim happened
    assert after.next_role == "" and not after.claimable
    assert after.plan is None  # the raw task prompt IS the plan


def test_edge_b_self_critique_uses_the_coder_model():
    bus = SilentBus(Settings())
    asyncio.run(bus.put_snapshot(_edge_snapshot("t-b1b", "B")))
    router = _Router([CODER_OUT], [APPROVED])
    w = _worker(bus, router, Role.PLANNER)
    _claim_and_run(bus, w, "t-b1b", "b:planner")
    # arm-B semantics: the SAME model critiques its own output
    assert router.calls[1] == ("critic", w.settings.model_coder)


def test_edge_b_revision_loop_is_bounded_by_max_revisions():
    bus = SilentBus(Settings())
    asyncio.run(bus.put_snapshot(_edge_snapshot("t-b2", "B", max_revisions=1)))
    router = _Router([CODER_OUT, CODER_OUT], [REJECTED, REJECTED])

    after = _claim_and_run(bus, _worker(bus, router, Role.PLANNER), "t-b2", "b:planner")
    assert after.state is TaskState.CODING  # rejected -> REVISE -> coder again

    after = _claim_and_run(bus, _worker(bus, router, Role.CODER), "t-b2", "b:coder")
    assert after.state is TaskState.FAILED  # revision budget exhausted
    assert [r for r, _ in router.calls] == ["coder", "critic", "coder", "critic"]
    assert not after.claimable


def test_edge_b_critic_question_parks_for_human():
    bus = SilentBus(Settings())
    asyncio.run(bus.put_snapshot(_edge_snapshot("t-b3", "B")))
    router = _Router([CODER_OUT], [QUESTION])
    after = _claim_and_run(bus, _worker(bus, router, Role.PLANNER), "t-b3", "b:planner")
    assert after.question == "which language?"
    assert not after.claimable


def test_default_edge_still_runs_the_planner():
    bus = SilentBus(Settings())
    asyncio.run(bus.put_snapshot(_edge_snapshot("t-n1", "")))
    plan_out = {"plan": [{"id": "s1", "description": "d"}], "notes": ""}
    router = _Router([], [], planner_output=plan_out)
    after = _claim_and_run(bus, _worker(bus, router, Role.PLANNER), "t-n1", "b:planner")
    assert after.state is TaskState.CODING
    assert after.plan is not None
    assert [r for r, _ in router.calls] == ["planner"]
