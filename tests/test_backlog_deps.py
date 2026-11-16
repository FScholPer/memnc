"""Tests for cross-store backlog dep resolution (mesh task ids)."""

from __future__ import annotations

import asyncio

import pytest

from memnc.backlog import BACKLOG_DONE, BacklogStore, BacklogTask


class _FakeKV:
    """In-memory KV double: minimal get/put/keys/create contract."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.revisions: dict[str, int] = {}

    async def get(self, key: str):
        if key not in self.data:
            raise KeyError(key)
        from types import SimpleNamespace

        return SimpleNamespace(value=self.data[key], revision=self.revisions[key])

    async def put(self, key: str, value: bytes) -> None:
        self.data[key] = value
        self.revisions[key] = self.revisions.get(key, 0) + 1

    async def keys(self) -> list[str]:
        return list(self.data)

    async def update(self, key: str, value: bytes, last: int) -> None:
        if self.revisions.get(key) != last:
            raise ValueError("wrong revision")  # CAS conflict
        await self.put(key, value)


class _FakeStore(BacklogStore):
    """BacklogStore with the NATS KV replaced by an in-memory double."""

    def __init__(self, tasks: list[BacklogTask]) -> None:  # noqa: D107
        self._kv = _FakeKV()
        for task in tasks:
            self._kv.data[task.id] = task.model_dump_json().encode()
            self._kv.revisions[task.id] = 1

    async def _ensure_kv(self):  # noqa: D102
        return self._kv


def _task(tid: str, status: str, deps: list[str] | None = None) -> BacklogTask:
    return BacklogTask(
        id=tid,
        title=f"task {tid}",
        prompt=f"prompt {tid}",
        status=status,
        deps=deps or [],
    )


def test_backlog_done_dep_satisfied_without_checker() -> None:
    store = _FakeStore(
        [
            _task("b-done", BACKLOG_DONE),
            _task("b-dep", "planned", deps=["b-done"]),
        ]
    )
    claimed = asyncio.run(store.next_claimable("driver-x"))
    assert claimed is not None and claimed.id == "b-dep"


def test_unknown_dep_blocked_without_checker() -> None:
    store = _FakeStore([_task("b-dep", "planned", deps=["b-missing"])])
    assert asyncio.run(store.next_claimable("driver-x")) is None


def _checker(mapping: dict[str, bool]):
    async def check(dep: str) -> bool:
        return mapping.get(dep, False)

    return check


@pytest.mark.parametrize(
    ("mesh_state_satisfied", "expected"),
    [
        (True, "b-dep"),
        (False, None),
    ],
)
def test_mesh_dep_resolved_via_checker(
    mesh_state_satisfied: bool, expected: str | None
) -> None:
    store = _FakeStore(
        [_task("b-dep", "planned", deps=["1a1emesh"])]
    )
    checker = _checker({"1a1emesh": mesh_state_satisfied})
    claimed = asyncio.run(store.next_claimable("driver-x", dep_checker=checker))
    assert (claimed.id if claimed else None) == expected


def test_checker_only_used_for_unknown_deps() -> None:
    """A b- dep not in the done set stays blocked even with a checker."""
    store = _FakeStore([_task("b-dep", "planned", deps=["b-other"])])
    checker = _checker({"b-other": True})
    assert asyncio.run(store.next_claimable("driver-x", dep_checker=checker)) is None