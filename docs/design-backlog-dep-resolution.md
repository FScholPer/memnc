# Design: Backlog dependency resolution across mesh tasks

## Problem

`BacklogStore.next_claimable()` only treats deps as satisfied when the dep id
belongs to a backlog entry with status `done`. Three real backlog entries were
injected with deps that reference **mesh task ids** (`1a1e...`, the mesh task
that first attempted the work). Those mesh tasks end in `failed` (terminal)
even when the work itself later succeeds through the backlog's own strong-retry
mechanism. Result: every planned entry stays blocked forever, the driver loops
idle, and the mesh stops building anything (observed 2026-11-16: 4 planned
entries, all blocked on terminal mesh tasks, driver idle for hours).

## Decision

A dependency is satisfied when **any** of the following holds:

1. the dep id is a backlog entry with status `done` (existing behaviour), or
2. the dep id resolves to a mesh task in a **terminal state** (done, failed,
   canceled).

Rationale: backlog deps injected as mesh task ids express ordering ("this slice
was attempted first"), not a success gate. A terminal-failed mesh attempt is
followed by backlog strong-retries that carry the work forward; blocking the
dependent entry on the failed attempt deadlocks the driver permanently. The
dependent task's own attempt budget and ship-gate remain the quality controls.

Unknown dep ids (neither backlog nor mesh registry) stay **blocked** —
conservative, no silent unblocking.

## Mechanics

- `BacklogStore.next_claimable(claimant, dep_checker=None)` — new optional
  async callable `dep_checker(dep_id) -> bool`. Backlog-only resolution stays
  the fast path (no checker argument, no behaviour change).
- The driver cycle (`cmd_backlog_run`) builds a lazy checker from
  `Bus.list_tasks()`: fetch snapshots once per cycle, only when a candidate has
  non-backlog deps, and cache them for that cycle.

## Test plan

Unit tests for `next_claimable` with a checker: mesh-dep done → satisfied,
failed → satisfied, active → blocked, unknown → blocked, backlog done dep still
satisfied without checker.

## Follow-ups (not in this slice)

- Intermittent `driver error: nats: no keys found` in the driver loop: likely
  a `keys()` call on an empty/missing bucket somewhere outside `list()`;
  needs its own root-cause pass.
- Old night/smoke test entries pollute the backlog; cleanup separately.
