"""Deterministic acceptance checks (15.4.5 gap 2).

The planner's free-text ``acceptance`` field on a PlanStep may carry
machine-checkable criteria, one per line:

    file_exists <path>
    file_min_size <path> <min_bytes>
    file_contains <path> <regex>
    command <argv>            (must exit 0)
    ffprobe_duration_min <path> <min_seconds>

Every check is a plain filesystem/probe call against the real workspace —
never LLM judgment. A failing check blocks the critic verdict from standing
and drives the normal revision loop with the check output as the issue text.
Lines that do not parse are recorded as ignored evidence, never silently
dropped: an acceptance field with prose only produces a no-op gate.

Security: ``command``/``ffprobe`` subprocesses get a curated env whitelist
(never os.environ wholesale), a bounded timeout, the whole process group is
killed on timeout, and output is read in bounded chunks.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import signal
from dataclasses import dataclass
from pathlib import Path

_CHECK_TIMEOUT_S = 30
_MAX_EVIDENCE = 2000


def _resolve_rel(root: Path, raw: str) -> Path | None:
    """Resolve a workspace-relative path. Planners sometimes emit absolute
    container paths (`/workspace/foo`) — normalize those to workspace-relative
    (the workspace root IS /workspace in the container). Any remaining escape
    via `..` is rejected. Returns None when the path escapes root."""
    stripped = raw.strip().lstrip("/")
    if not stripped:
        return None
    parts = stripped.split("/")
    if parts and parts[0].lower() == "workspace":
        parts = parts[1:]
        stripped = "/".join(parts)
    if not stripped:
        return None
    resolved = (root / stripped).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        return None
    return resolved

_SUBPROC_ENV = {
    k: os.environ[k]
    for k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
    if k in os.environ
}


@dataclass
class AcceptanceCriterion:
    kind: str
    args: list[str]


@dataclass
class CheckResult:
    line: str
    ok: bool
    evidence: str


@dataclass
class AcceptanceReport:
    results: list[CheckResult]

    @property
    def all_passed(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def evidence(self) -> str:
        text = "\n".join(f"[{'PASS' if r.ok else 'FAIL'}] {r.line}: {r.evidence}" for r in self.results)
        return text[:_MAX_EVIDENCE]


def parse_criteria(text: str) -> tuple[list[AcceptanceCriterion], list[str]]:
    """Parse an acceptance field into criteria plus unparsable lines."""
    criteria: list[AcceptanceCriterion] = []
    unparsed: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = shlex.split(line)
        if not parts:
            unparsed.append(line)
            continue
        kind = parts[0].lower().replace("-", "_")
        args = parts[1:]
        spec = _CHECK_ARITY.get(kind)
        if spec is None:
            unparsed.append(line)
            continue
        lo, hi = spec
        if len(args) < lo or (hi is not None and len(args) > hi):
            unparsed.append(line)
            continue
        criteria.append(AcceptanceCriterion(kind=kind, args=args))
    return criteria, unparsed


async def run_acceptance(criteria: list[AcceptanceCriterion], root: Path) -> AcceptanceReport:
    results: list[CheckResult] = []
    for c in criteria:
        fn = _CHECKS[c.kind]
        results.append(await fn(c.args, root))
    return AcceptanceReport(results=results)


async def _check_file_exists(args: list[str], root: Path) -> CheckResult:
    line = f"file_exists {args[0]}"
    path = _resolve_rel(root, args[0])
    if path is None:
        return CheckResult(line, False, f"invalid path (escapes workspace): {args[0]}")
    if path.is_file():
        return CheckResult(line, True, f"{args[0]} exists ({path.stat().st_size} bytes)")
    return CheckResult(line, False, f"{args[0]} missing from workspace")


async def _check_file_min_size(args: list[str], root: Path) -> CheckResult:
    line = f"file_min_size {args[0]} {args[1]}"
    try:
        min_bytes = int(args[1])
    except ValueError:
        return CheckResult(line, False, f"invalid byte count {args[1]!r}")
    path = _resolve_rel(root, args[0])
    if path is None:
        return CheckResult(line, False, f"invalid path (escapes workspace): {args[0]}")
    if not path.is_file():
        return CheckResult(line, False, f"{args[0]} missing from workspace")
    size = path.stat().st_size
    return CheckResult(line, size >= min_bytes, f"{args[0]} is {size} bytes (min {min_bytes})")


async def _check_file_contains(args: list[str], root: Path) -> CheckResult:
    line = f"file_contains {args[0]} {args[1]}"
    try:
        pattern = re.compile(args[1])
    except re.error as exc:
        return CheckResult(line, False, f"invalid regex: {exc}")
    path = _resolve_rel(root, args[0])
    if path is None:
        return CheckResult(line, False, f"invalid path (escapes workspace): {args[0]}")
    if not path.is_file():
        return CheckResult(line, False, f"{args[0]} missing from workspace")
    try:
        text = path.read_text(errors="replace")[: 10_000_000]
    except OSError as exc:
        return CheckResult(line, False, f"read failed: {exc}")
    return CheckResult(line, bool(pattern.search(text)), f"regex {args[1]!r} in {args[0]}")


async def _run_argv(argv: list[str], root: Path) -> tuple[int, str, str]:
    """Run argv in the workspace with a curated env; (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(root),
            env=_SUBPROC_ENV,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        return -1, "", f"binary not found: {argv[0]}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=_CHECK_TIMEOUT_S)
    except TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        return -1, "", "TIMEOUT"
    code = proc.returncode if proc.returncode is not None else -1
    return code, out.decode(errors="replace")[:4000], err.decode(errors="replace")[:4000]


async def _check_command(args: list[str], root: Path) -> CheckResult:
    line = f"command {' '.join(args)}"
    code, out, err = await _run_argv(args, root)
    evidence = f"exit={code}" + (f" stdout: {out.strip()[:300]}" if out.strip() else "")
    if err.strip():
        evidence += f" stderr: {err.strip()[:300]}"
    return CheckResult(line, code == 0, evidence)


async def _check_ffprobe_duration(args: list[str], root: Path) -> CheckResult:
    line = f"ffprobe_duration_min {args[0]} {args[1]}"
    try:
        min_seconds = float(args[1])
    except ValueError:
        return CheckResult(line, False, f"invalid duration {args[1]!r}")
    path = _resolve_rel(root, args[0])
    if path is None:
        return CheckResult(line, False, f"invalid path (escapes workspace): {args[0]}")
    if not path.is_file():
        return CheckResult(line, False, f"{args[0]} missing from workspace")
    code, out, err = await _run_argv(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        root,
    )
    if err.strip() == "TIMEOUT":
        return CheckResult(line, False, "ffprobe TIMEOUT")
    if code != 0:
        return CheckResult(line, False, f"ffprobe exit={code} {err.strip()[:200]}")
    try:
        duration = float(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return CheckResult(line, False, f"unparsable ffprobe output {out.strip()[:100]!r}")
    return CheckResult(line, duration >= min_seconds, f"duration={duration:.2f}s (min {min_seconds})")


_CHECK_ARITY: dict[str, tuple[int, int | None]] = {
    "file_exists": (1, 1),
    "file_min_size": (2, 2),
    "file_contains": (2, 2),
    "command": (1, None),
    "ffprobe_duration_min": (2, 2),
}
_CHECKS = {
    "file_exists": _check_file_exists,
    "file_min_size": _check_file_min_size,
    "file_contains": _check_file_contains,
    "command": _check_command,
    "ffprobe_duration_min": _check_ffprobe_duration,
}


async def acceptance_from_steps(steps, root: Path) -> tuple[AcceptanceReport, int]:
    """Run every parseable criterion found in plan-step acceptance fields.

    Returns (report, total_criteria). Steps with prose-only acceptance add
    ignored-line evidence; a plan without any parseable criterion yields an
    empty report (no-op gate).
    """
    criteria: list[AcceptanceCriterion] = []
    unparsed: list[str] = []
    for step in steps:
        text = getattr(step, "acceptance", "")
        if not text:
            continue
        got, missed = parse_criteria(text)
        criteria.extend(got)
        unparsed.extend(f"step {getattr(step, 'id', '?')}: {m}" for m in missed)
    report = await run_acceptance(criteria, root)
    if unparsed:
        note = CheckResult(
            line="unparsed acceptance lines (ignored, not verified)",
            ok=True,
            evidence="\n".join(unparsed)[:_MAX_EVIDENCE],
        )
        report.results.append(note)
    return report, len(criteria)
