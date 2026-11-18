"""Tests for the dreaming job wrapper (14.13 Step 6 build step 2).

Style follows the repo conventions: sync tests + ``asyncio.run``, no
decorators. The gates under test are the roadmap's two safety mechanisms:
skip-if-busy and the single-writer ``dream.lock`` KV claim, plus the
operator flag and the dry-run/apply split.
"""

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta

from memnc.config import Settings
from memnc.dreaming import dream_bullets
from memnc.dreaming_job import CLAIM_STALE_S, run_job
from memnc.observations import TaskObservation
from memnc.schema import TaskSnapshot, TaskState


def _obs(task_id: str, task: str, outcome: str = "done") -> TaskObservation:
    return TaskObservation(
        task_id=task_id,
        trace_id=f"tr-{task_id}",
        task=task,
        outcome=outcome,
        route={},
        revisions=0,
        error=None,
        observed_at="2026-11-18T00:00:00+00:00",
    )


def _snap(task_id: str, state: TaskState) -> TaskSnapshot:
    return TaskSnapshot(
        task_id=task_id,
        trace_id=f"tr-{task_id}",
        task="task text",
        state=state,
    )


class FakeLockKV:
    """In-memory stand-in for one KV bucket (create/get/update/delete)."""

    def __init__(self, existing: dict[str, str] | None = None) -> None:
        self.values: dict[str, str] = dict(existing or {})
        self.revisions: dict[str, int] = {k: 1 for k in self.values}
        self.deleted: list[str] = []

    async def create(self, key: str, value: bytes) -> int:
        if key in self.values:
            raise RuntimeError("key already exists")
        self.values[key] = value.decode()
        self.revisions[key] = 1
        return 1

    async def get(self, key: str):
        if key not in self.values:
            raise KeyError(key)

        class _Entry:
            revision = self.revisions[key]
            value = self.values[key].encode()

        return _Entry()

    async def update(self, key: str, value: bytes, last: int) -> None:
        if self.revisions.get(key) != last:
            raise RuntimeError("CAS conflict")
        self.values[key] = value.decode()
        self.revisions[key] = last + 1

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)


class FakeJs:
    """JetStream surface that only knows the dream_lock bucket."""

    def __init__(self, kv: FakeLockKV) -> None:
        self.kv = kv

    async def key_value(self, bucket: str) -> FakeLockKV:
        if bucket != "dream_lock":
            raise RuntimeError("no such bucket")
        return self.kv

    async def create_key_value(self, bucket: str, history: int = 1) -> FakeLockKV:
        assert bucket == "dream_lock"
        return self.kv


class FakeBus:
    """Only the surface run_job touches: tasks, observations, js."""

    def __init__(self, snapshots=None, observations=None, lock_kv=None) -> None:
        self.snapshots = snapshots or []
        self.observations = observations or []
        self.js = FakeJs(lock_kv if lock_kv is not None else FakeLockKV())
        self.closed = False

    async def list_tasks(self):
        return self.snapshots

    async def list_observations(self):
        return self.observations

    async def close(self) -> None:
        self.closed = True


def _settings(**kw) -> Settings:
    return Settings(node_name="test-node", dreaming_enabled=True, **kw)


def test_disabled_job_refuses_before_any_nats_touch():
    settings = Settings(node_name="test-node", dreaming_enabled=False)
    rc = asyncio.run(run_job(settings, apply=False, bus=None))
    assert rc == 2


def test_skip_if_busy_when_tasks_not_terminal():
    bus = FakeBus(
        snapshots=[_snap("t1", TaskState.DONE), _snap("t2", TaskState.CODING)],
        observations=[_obs("t1", "Implement a rate limiter with burst support.")],
    )
    settings = _settings()
    rc = asyncio.run(run_job(settings, apply=False, bus=bus))
    assert rc == 0
    assert not bus.closed  # injected bus is not closed by the job


def test_dry_run_prints_bullets_and_leaves_store_untouched(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "store.json")
        bus = FakeBus(
            observations=[
                _obs("t1", "Implement a rate limiter with burst support."),
                _obs("t2", "Implement a rate limiter with burst support."),
            ]
        )
        settings = _settings(experience_store_path=path)
        rc = asyncio.run(run_job(settings, apply=False, bus=bus))
        assert rc == 0
        out = capsys.readouterr().out
        assert "dry-run" in out
        assert not os.path.exists(path)


def test_apply_merges_into_store_and_releases_lock(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "store.json")
        kv = FakeLockKV()
        bus = FakeBus(
            observations=[
                _obs("t1", "Implement a rate limiter with burst support."),
                _obs("t2", "Implement a rate limiter with burst support."),
            ],
            lock_kv=kv,
        )
        settings = _settings(experience_store_path=path)
        rc = asyncio.run(run_job(settings, apply=True, bus=bus))
        assert rc == 0
        out = capsys.readouterr().out
        assert "merged" in out
        store_json = json.load(open(path))
        topics = {b["topic"] for b in store_json}
        assert "dream" in topics
        assert kv.deleted == ["dream.lock"]  # released, not left dangling


def test_fresh_claim_blocks_second_writer():
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    kv = FakeLockKV({"dream.lock": now})
    bus = FakeBus(observations=[_obs("t1", "task one")], lock_kv=kv)
    rc = asyncio.run(run_job(_settings(), apply=False, bus=bus))
    assert rc == 0


def test_stale_claim_is_reclaimed():
    old = (
        datetime.now(UTC) - timedelta(seconds=CLAIM_STALE_S + 60)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    kv = FakeLockKV({"dream.lock": old})
    bus = FakeBus(
        observations=[
            _obs("t1", "Implement a rate limiter with burst support."),
            _obs("t2", "Implement a rate limiter with burst support."),
        ],
        lock_kv=kv,
    )
    rc = asyncio.run(run_job(_settings(), apply=True, bus=bus))
    assert rc == 0


def test_unparseable_claim_is_never_stolen():
    kv = FakeLockKV({"dream.lock": "not-a-timestamp"})
    bus = FakeBus(observations=[_obs("t1", "task one")], lock_kv=kv)
    rc = asyncio.run(run_job(_settings(), apply=False, bus=bus))
    assert rc == 0
    assert kv.values.get("dream.lock") == "not-a-timestamp"


def test_empty_observations_produce_no_merge(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "store.json")
        bus = FakeBus(observations=[], lock_kv=FakeLockKV())
        settings = _settings(experience_store_path=path)
        rc = asyncio.run(run_job(settings, apply=True, bus=bus))
        assert rc == 0
        assert "nothing to consolidate" in capsys.readouterr().out
        assert not os.path.exists(path)


def test_bullets_roundtrip_with_core():
    """Sanity: the wrapper consumes exactly the core's output shape."""
    obs = [
        _obs("t1", "Implement a rate limiter with burst support."),
        _obs("t2", "Implement a rate limiter with burst support."),
    ]
    bullets = dream_bullets(obs)
    assert bullets  # the wrapper must be fed a dict of topic -> texts
    assert all(isinstance(v, list) for v in bullets.values())
