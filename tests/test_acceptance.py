"""Tests for deterministic acceptance checks (15.4.5 gap 2)."""

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

from memnc.acceptance import (
    AcceptanceCriterion,
    acceptance_from_steps,
    parse_criteria,
    run_acceptance,
)


def _plan_step(step=1, description="do it", acceptance=""):
    from memnc.schema import PlanStep

    return PlanStep(step=step, description=description, acceptance=acceptance)


def _critique(approved=True):
    from memnc.schema import CriticOutput

    return CriticOutput(approved=approved, summary="looks fine")


def _snapshot(plan_steps=None, critique=None):
    from memnc.schema import PlannerOutput, PlanStep, TaskSnapshot

    plan = PlannerOutput(plan=[PlanStep(step=1, description="d", acceptance="")])
    if plan_steps:
        plan = PlannerOutput(plan=plan_steps)
    snap = TaskSnapshot(task_id="t-acc-1", trace_id="tr-1", task="task")
    snap.plan = plan
    snap.critique = critique
    return snap


class TestParseCriteria:
    def test_valid_lines(self):
        text = (
            "file_exists out/report.md\n"
            "file_min_size out/report.md 100\n"
            "FILE-CONTAINS out/report.md ^Status\n"
            "command python --version\n"
            "ffprobe_duration_min audio.wav 30\n"
        )
        criteria, unparsed = parse_criteria(text)
        assert unparsed == []
        assert [c.kind for c in criteria] == [
            "file_exists",
            "file_min_size",
            "file_contains",
            "command",
            "ffprobe_duration_min",
        ]
        assert criteria[2].args == ["out/report.md", "^Status"]

    def test_unknown_kind_and_arity_go_unparsed(self):
        criteria, unparsed = parse_criteria(
            "file_exists a.md\nmake_it_so now\nfile_min_size only_one_arg\n\n"
        )
        assert len(criteria) == 1
        assert unparsed == ["make_it_so now", "file_min_size only_one_arg"]

    def test_empty_and_prose(self):
        criteria, unparsed = parse_criteria("")
        assert criteria == [] and unparsed == []
        criteria, unparsed = parse_criteria("the report must look nice")
        assert criteria == [] and unparsed == ["the report must look nice"]


class TestRunChecks:
    def test_file_checks(self, tmp_path: Path):
        (tmp_path / "out").mkdir()
        (tmp_path / "out" / "report.md").write_text("Status: ok\n" * 20)
        criteria, _ = parse_criteria(
            "file_exists out/report.md\nfile_min_size out/report.md 50\n"
            "file_contains out/report.md 'Status: ok'\nfile_contains out/report.md nope-nope\n"
            "file_min_size out/report.md 100000\nfile_min_size missing.md 5\n"
        )
        report = asyncio.run(run_acceptance(criteria, tmp_path))
        flags = [r.ok for r in report.results]
        assert flags == [True, True, True, False, False, False]
        assert "missing from workspace" in report.results[5].evidence
        assert not report.all_passed

    def test_command_checks(self, tmp_path: Path):
        criteria, _ = parse_criteria("command true\ncommand false\ncommand definitely-no-binary-xyz\n")
        report = asyncio.run(run_acceptance(criteria, tmp_path))
        assert [r.ok for r in report.results] == [True, False, False]
        assert "binary not found" in report.results[2].evidence

    def test_regex_error_reported(self, tmp_path: Path):
        criteria, _ = parse_criteria("file_contains a.md ([unclosed")
        report = asyncio.run(run_acceptance(criteria, tmp_path))
        assert not report.results[0].ok
        assert "invalid regex" in report.results[0].evidence

    def test_ffprobe_duration(self, tmp_path: Path):
        (tmp_path / "kugel.wav").write_bytes(b"RIFFnotaudio")
        criteria, _ = parse_criteria("ffprobe_duration_min kugel.wav 30\n")
        report = asyncio.run(run_acceptance(criteria, tmp_path))
        r = report.results[0]
        if shutil.which("ffprobe"):
            assert not r.ok  # garbage file cannot yield a duration
        else:
            assert not r.ok and "binary not found" in r.evidence


class TestAcceptanceFromSteps:
    def test_noop_when_no_criteria(self, tmp_path: Path):
        steps = [_plan_step(acceptance="report must exist"), _plan_step(acceptance="")]
        report, total = asyncio.run(acceptance_from_steps(steps, tmp_path))
        assert total == 0
        assert report.all_passed
        # prose is recorded as ignored evidence, never silently dropped
        assert "ignored" in report.evidence
        assert "report must exist" in report.evidence

    def test_runs_all_steps(self, tmp_path: Path):
        (tmp_path / "a.md").write_text("x" * 10)
        steps = [
            _plan_step(1, acceptance="file_exists a.md"),
            _plan_step(2, acceptance="file_min_size a.md 5"),
        ]
        report, total = asyncio.run(acceptance_from_steps(steps, tmp_path))
        assert total == 2
        assert report.all_passed


class TestWorkerGate:
    def _worker_like(self, tmp_path: Path):
        from memnc.config import Settings
        from memnc.worker import Worker

        settings = Settings(workspace_root=str(tmp_path))
        return Worker.__dict__["_acceptance_gate"], SimpleNamespace(settings=settings)

    def test_failing_check_overrides_approval(self, tmp_path: Path):
        gate, fake_self = self._worker_like(tmp_path)

        snap = _snapshot(
            plan_steps=[_plan_step(acceptance="file_exists nowhere.md")],
            critique=_critique(),
        )
        asyncio.run(gate(fake_self, snap))
        assert snap.critique.approved is False
        assert snap.critique.issues[0].severity == "blocker"
        assert "acceptance check failed" in snap.critique.issues[0].issue
        assert snap.acceptance.startswith("[FAIL]")
        assert any(e.event == "acceptance.failed" for e in snap.history)

    def test_passing_checks_keep_approval(self, tmp_path: Path):
        gate, fake_self = self._worker_like(tmp_path)
        (tmp_path / "ok.md").write_text("fine")

        snap = _snapshot(
            plan_steps=[_plan_step(acceptance="file_exists ok.md")],
            critique=_critique(),
        )
        asyncio.run(gate(fake_self, snap))
        assert snap.critique.approved is True
        assert "[PASS]" in snap.acceptance

    def test_no_criteria_is_noop(self, tmp_path: Path):
        gate, fake_self = self._worker_like(tmp_path)

        snap = _snapshot(critique=_critique())
        asyncio.run(gate(fake_self, snap))
        assert snap.critique.approved is True

    def test_no_critic_verdict_is_noop(self, tmp_path: Path):
        gate, fake_self = self._worker_like(tmp_path)
        snap = _snapshot(plan_steps=[_plan_step(acceptance="file_exists x.md")])
        asyncio.run(gate(fake_self, snap))
        assert snap.critique is None

    def test_gate_disabled(self, tmp_path: Path, monkeypatch):
        from memnc.config import Settings
        from memnc.worker import Worker

        settings = Settings(workspace_root=str(tmp_path), acceptance_gate=False)
        fake_self = SimpleNamespace(settings=settings)
        snap = _snapshot(
            plan_steps=[_plan_step(acceptance="file_exists nowhere.md")],
            critique=_critique(),
        )
        asyncio.run(Worker.__dict__["_acceptance_gate"](fake_self, snap))
        assert snap.critique.approved is True


def test_criterion_dataclass_shape():
    c = AcceptanceCriterion(kind="file_exists", args=["a.md"])
    assert c.kind == "file_exists" and c.args == ["a.md"]


def test_absolute_and_escape_paths(tmp_path: Path):
    """Planner emits absolute `/workspace/...` (real 1a1ed936 failure) and a
    traversal escape; both must resolve/behave deterministically."""
    (tmp_path / "attempt-journal.json").write_text('{"last_stage": "CODING"}\n')
    criteria, _ = parse_criteria(
        "file_exists /workspace/attempt-journal.json\n"
        "file_contains /workspace/attempt-journal.json stage\n"
        "file_exists ../../../etc/passwd\n"
        "file_exists /\n"
    )
    report = asyncio.run(run_acceptance(criteria, tmp_path))
    r = report.results
    # absolute path normalized to workspace-relative -> file found
    assert r[0].ok, r[0].evidence
    assert r[1].ok, r[1].evidence
    # `..` escape rejected, not silently resolved outside the workspace
    assert not r[2].ok and "escapes workspace" in r[2].evidence
    # bare root path rejected
    assert not r[3].ok and "escapes workspace" in r[3].evidence
    assert not report.all_passed


def test_absolute_escape_checked(tmp_path: Path):
    (tmp_path / "secret.md").write_text("secret\n")
    criteria, _ = parse_criteria("file_exists ../../secret.md\n")
    report = asyncio.run(run_acceptance(criteria, tmp_path))
    assert not report.results[0].ok
    assert "escapes workspace" in report.results[0].evidence


def test_workspace_root_criterion(tmp_path: Path):
    """Planner emits `file_exists /workspace` — the root itself must pass."""
    criteria, _ = parse_criteria("file_exists /workspace\n")
    report = asyncio.run(run_acceptance(criteria, tmp_path))
    assert report.results[0].ok, report.results[0].evidence
