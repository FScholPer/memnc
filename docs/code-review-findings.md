# Code review: findings and fix plan

Scope: the last five commits (`e7e4653`..`b07ac0d`) and the code paths they
touch, plus a security pass over the compose stack, the worker, and the MCP
shell tool. Not yet reviewed in depth: `orchestrator.py`, `bus.py`,
`telegram.py`, the rest of `cli.py`, `control/`, `bench/`. Lint and tests were
not run (CI covers them).

Severity: **High** = security or silent data loss. **Medium** = wrong
behaviour or policy violation. **Low** = hygiene.

## Findings

### F1 (High) Shell output cap drops almost all output

`src/memnc/mcp/tools_shell.py`, `_read_stream`. The cap compares
`len(chunks) * 65536 < limit`, which counts chunks, not bytes. Once two reads
have happened, every later read is discarded.

Reproduced with a copy of the function: a loop printing 2000 lines returned
**6 bytes** and `truncated: True`. The coder's shell tool therefore sees only
the first pipe read of any command with incremental output.

### F2 (High) Model-written code runs with the worker's full environment

`src/memnc/worker.py`, `_verify_gate` (around lines 1500 and 1523). Both
`create_subprocess_exec` calls omit `env=`, so the child inherits everything
in `os.environ`: `NATS_TOKEN`, `INFERENCE_API_KEY`, fallback keys. The shell
tool deliberately strips these (`tools_shell.py` lines 37-41). Here the
executed code is LLM output.

Also: on timeout, `proc.kill()` is not followed by `await proc.wait()`, which
can leave zombies.

### F3 (High) Every mesh service mounts all NKey seeds

`docker-compose.yml`, `x-mesh-service` volumes: `./secrets:/secrets:ro`. Every
worker (planner, coder, critic) and the telegram container can read every
node's seed, including `orchestrator.nk`. The telegram comment says it "must
stay mounted" only for its own seed.

This breaks the per-role scope rule in AGENTS.md and `security.md`.

### F4 (High) Host SSH keys and opencode config reach LLM-driven shell

`docker-compose.yml`, `x-mesh-service` volumes:
- `${HOME}/.ssh:/tmp/.ssh:ro` (private keys)
- `${HOME}/.config/opencode:/opt/opencode-config:ro` (provider config, may
  contain keys)

These are mounted into every worker. `ALLOW_SHELL` defaults to `true`
(`config.py` line 133, compose line 35), so a coder model can read them.
Ssh access for node-c is needed by one role only.

### F5 (Medium) Hardcoded `dev-token` default for NATS

`src/memnc/discuss/common.py` line 16:
`os.environ.get("NATS_TOKEN", "dev-token")`. The compose file says "never
ship a default", and this default is then used by `discuss/agent.py`, which
suppresses the leak scan with `# leakscan:ok`.

### F6 (Medium) Watch timeout can run the same task twice

`src/memnc/cli.py`, `_driver_cycle`. On `TimeoutError` the driver marks the
attempt failed and retries, but the mesh task keeps running. The retry then
runs concurrently with the orphan. Nothing cancels or checks the first task.

The timeout itself is correct (it fixes the wedged-driver bug).

### F7 (Medium) NATS ports published on all interfaces

`docker-compose.yml`, `nats` service: `"4222:4222"` and `"6222:6222"` bind to
0.0.0.0. Client auth is token or NKey, but cluster routes on 6222 need their
own authorization. This is not verified in the repo, since `secrets/nats.conf`
is not tracked.

### F8 (Medium) German text in shipped code

`src/memnc/roadmap.py` lines 51-54: the generated backlog prompt is German
("Kontext laut Roadmap", "Lies zuerst", "fuer", "Aendere"). AGENTS.md requires
English in source. This text is sent to the coder model as the task prompt.

### F9 (Low) Stray tracked copy of a module

`test_backup.py.tmp/` is committed at the repo root with `src/memnc/synergy.py`
and `tests/test_synergy.py`. Its `synergy.py` differs from `src/memnc/synergy.py`.
It violates the layout rule and is dead code.

### F10 (Low) Unpinned installer in the worker image

`docker/worker.Dockerfile`: `curl -fsSL https://opencode.ai/install | bash`.
No version pin or checksum, so the image changes on every build.

### F11 (Low) Pheromone slug change orphans existing keys

`src/memnc/pheromone.py`: `_SLUG_BAD` now strips `.` and `_`. Live task-type
keys written before `c9452d5` no longer match the new slugs. Old entries stay
in the KV bucket and are not migrated.

## Fix plan

Order is by risk. Each step is one conventional commit with an English message
and a test. Tests must not depend on the host.

### Phase 1: security (F2, F3, F4, F5)

1. **F2, verify-gate env.** Build the child env with the same allow-list as
   `run_shell` (`PATH`, `HOME`, `LANG`, `TERM`, `TMPDIR`). Pass `env=`.
   Await `proc.wait()` after `kill()`. Extract a shared helper so the two
   paths cannot drift.
   Test: a child started by `_verify_gate` cannot see `NATS_TOKEN` or
   `INFERENCE_API_KEY` (set them in the test env, pin them).

2. **F3, per-role seed mounts.** Replace `./secrets:/secrets:ro` in
   `x-mesh-service` with per-service mounts of only that node's seed file, e.g.
   `./secrets/${NODE_NAME}-coder.nk:/secrets/${NODE_NAME}-coder.nk:ro`. Remove
   the shared mount from the telegram service too. Keep `nats.conf` on the
   nats service only.
   Test: compose config check (`docker compose config`) lists only the expected
   seed per service.

3. **F4, narrow host mounts.** Mount `~/.ssh` only into the worker that needs
   cross-node ops (behind an explicit compose profile or override file), and
   drop `~/.config/opencode` from roles that don't run the opencode harness.
   Longer term: move `ssh` access to a dedicated, non-coder role. Decide with
   the owner whether `ALLOW_SHELL` should default to `false` for coders.
   Test: compose config review; no test needed beyond that.

4. **F5, remove the default.** `discuss/common.py`: require `NATS_TOKEN`, fail
   with a clear error if missing (same pattern as compose `${NATS_TOKEN:?...}`).
   Remove the `# leakscan:ok` exception in `discuss/agent.py` once the value is
   no longer a literal.
   Test: missing env raises; set env works.

### Phase 2: correctness (F1, F6)

5. **F1, byte-based cap.** Track `total` bytes in `_read_stream`:
   `room = limit - total`, append `chunk[:room]`, set `truncated` when data is
   dropped, and keep draining to EOF.
   Test: a process emitting ~200 KB in small writes returns exactly
   `max_output_bytes` and `truncated=True`; a 10-line output returns all lines
   and `truncated=False`.

6. **F6, no duplicate runs.** On watch timeout, cancel the mesh task before
   marking the attempt failed. If the bus has no cancel, record the orphan
   `mesh_task_id` in the backlog entry and skip retry until it is terminal.
   This needs a decision: confirm the cancel subject with the owner first.
   Test: driver with a fake watch that never resolves marks the attempt failed
   and does not submit a second mesh task while the first is still open.

### Phase 3: policy and hygiene (F7, F8, F9, F10, F11)

7. **F7, bind NATS ports.** Bind `4222` to the LAN address only if needed,
   otherwise `127.0.0.1`. Keep `6222` on the cluster-only network, not
   published, unless `topology: mesh` needs it. Document the cluster auth in
   `docs/` (not in the repo's secrets). Confirm with the owner before changing
   a deployed topology.

8. **F8, translate.** Replace the German strings in `roadmap.py` with English.
   Test: `test_roadmap.py` asserts the English prompt text.

9. **F9, remove the stray copy.** Delete `test_backup.py.tmp/` after a diff
   against `src/memnc/synergy.py` confirms nothing is missing from the real
   module. Use `git rm -r`.

10. **F10, pin opencode.** Pin a version in the Dockerfile
    (`opencode-ai@<version>` or the release tarball) and verify a sha256.

11. **F11, pheromone migration.** Add a one-off step that re-keys or drops
    entries under the old slug format, or document that the old bucket is
    dropped on upgrade. Confirm with the owner before deleting data.

## Not yet reviewed

Before closing this review, read: `orchestrator.py` (1541 lines), `bus.py`
(1001), `telegram.py` (1068), the rest of `cli.py`, `control/`, and
`bench/runner.py`. Pay attention to the same classes of issue as F1-F4: output
caps, env inheritance, and secrets in mounts or logs.
