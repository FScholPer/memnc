# memnc Roadmap

Structured plan: **chapters are epics**, each epic owns **tasks**. The old
chronological log is unchanged in [`roadmap-log.md`](roadmap-log.md) — every
task below carries a `Log` reference (`§19.2`, `§14.15.10`, …) so the evidence,
measurements and design notes stay one click away.

Status: `✅` built and verified · `🟡` partial / running · `☐` open · `⏸` deferred · `🗑` dropped

## Contents

| Epic | Title | Tasks | Open |
|---|---|---|---|
| [E1](#e1-substrate-and-mesh-core) | Substrate and mesh core | T1.1–T1.6 | 3 |
| [E2](#e2-harness-layer) | Harness layer | T2.1–T2.6 | 1 |
| [E3](#e3-backlog-and-scheduling) | Backlog and scheduling | T3.1–T3.10 | 3 |
| [E4](#e4-task-safety-and-human-in-the-loop) | Task safety and human-in-the-loop (P5) | T4.1–T4.6 | 5 |
| [E5](#e5-elite-evolution) | ELITE evolution (P6) | T5.1–T5.6 | 6 |
| [E6](#e6-routing-and-learning) | Routing and learning (P7) | T6.1–T6.6 | 6 |
| [E7](#e7-model-intelligence) | Model intelligence | T7.1–T7.5 | 1 |
| [E8](#e8-operator-surfaces) | Operator surfaces | T8.1–T8.8 | 2 |
| [E9](#e9-fleet-and-model-serving) | Fleet and model serving | T9.1–T9.6 | 3 |
| [E10](#e10-distribution-and-docs) | Distribution and docs | T10.1–T10.5 | 1 |

**Ordering rule (binding, §19.0):** P5/E4 before E5, E5 before E6. Nothing in
E5/E6 starts until the #35 acceptance criteria 1–3 pass on the live two-node
mesh. Deterministic checks run before model judgement everywhere.

**Next up:** T4.1 → T4.2 → T4.3 → T4.4 (see [Sequencing](#sequencing)).

---

## E1 Substrate and mesh core

Bus, FSM, attribution, node membership.

| ID | Task | Status | Evidence / log |
|---|---|---|---|
| T1.1 | JetStream task subjects, snapshots, terminal events | ✅ | M8 clearance, §P0 |
| T1.2 | Signed attribution and grants (single-writer `mesh.control`) | ✅ | §14.4, §14.14 |
| T1.3 | M14/M15/M16 live tests on both nodes | ✅ | §P2a |
| T1.4 | node-c (192.168.8.180) back on the bus and on mesh HEAD | 🟡 | §15.4.2; mesh task `1a212915d13e…` |
| T1.5 | Durable tasks instead of one-shot (retry, error-aware) | 🟡 | §15.4.3 — slice 1 shipped |
| T1.6 | VRAM as a schedulable resource on the admission path | ☐ | §15.4.6 (design note) |

## E2 Harness layer

One worker, several harnesses; per-task selection.

| ID | Task | Status | Evidence / log |
|---|---|---|---|
| T2.1 | Harness abstraction, pi as first harness | ✅ | §14.14 |
| T2.2 | `ToolLoopHarness` (in-proc reference implementation) | ✅ | §14.14 |
| T2.3 | Per-task harness selection (slice 3) | ✅ | §14.14 |
| T2.4 | OpenCode harness, also inside the worker container | ✅ | §15.4, §15.4.1 |
| T2.5 | Discuss harness with tests | ✅ | §16.5 |
| T2.6 | Foreign-harness tool/approval routing over NATS | ☐ | P5.5 (§19.2) |

## E3 Backlog and scheduling

Plan-level queue, claims, routing by evidence.

| ID | Task | Status | Evidence / log |
|---|---|---|---|
| T3.1 | Backlog queue with atomic claim | ✅ | §14.15 |
| T3.2 | Plan-decomposition helper `backlog-plan` | ✅ | §14.15.1 |
| T3.3 | Weak→strong routing via backlog attempts | ✅ | §14.15.3 |
| T3.4 | Failed-chain semantics | ✅ | §14.15.5 |
| T3.5 | Daemonized backlog driver | ✅ | §14.15.4 |
| T3.6 | Wake-on-backlog / WOL | ✅ | §14.15.8 |
| T3.7 | Cross-task dependency resolution | ✅ | §14.15.12 |
| T3.8 | Swarm-vs-single benchmark | ☐ | §14.15.9 |
| T3.9 | Pheromone routing: init, selection, worker honouring, decay | 🟡 | §14.15.10 — consolidation open |
| T3.10 | Swarm-to-human legibility (discussion first) | ☐ | §14.15.11 |

## E4 Task safety and human-in-the-loop

Phase 1. The safety valve for everything after it (issue #35, #37 Phase 1B).

| ID | Task | Status | Evidence / log |
|---|---|---|---|
| T4.1 | Lease by liveness: heartbeat-age reap, explicit requeue event, DLQ | ☐ | P5.1 (§19.2) |
| T4.2 | Mid-turn suspension: `AWAITING_HUMAN` + continuation cursor | ☐ | P5.2 (§19.2) |
| T4.3 | Pre-act guard: per-role tool allowlists, destructive-command cards | ☐ | P5.3 (§19.2) |
| T4.4 | Blinded critic JSON gate, duplicate-diff escalation | ☐ | P5.4 (§19.2) |
| T4.5 | Confidence-triggered ask (deterministic triggers only in v1) | ☐ | P5.6 (§19.2) |
| T4.6 | `WAIT_INPUT` ask/answer, clarify-before-human, `meshctl answer` | ✅ | §19.1 (built, but slot-parking) |

**Exit criteria:** kill -9 a coder mid-task → exactly one `claim.reaped`, one
completion, no duplicate DONE; park mid-tool-loop, answer 30 min later, no
repeated side effects; no destructive shell command without a human approval
event in the ledger.

## E5 ELITE evolution

Issue #36, on the existing planner→coder→critic pools. No new roles.

| ID | Task | Status | Evidence / log |
|---|---|---|---|
| T5.1 | `meshctl evolve` + evolution ledger (`m_t`) | ☐ | P6.1 (§19.3) |
| T5.2 | Worktree sandboxing, one tree per hypothesis | ☐ | P6.2 |
| T5.3 | Deterministic gates `G_exp` / `G_acc` (zero LLM calls) | ☐ | P6.3 |
| T5.4 | Synthesis prompt: classify children, reimplement `Δ_int` | ☐ | P6.4 |
| T5.5 | Per-generation observability, `/approve_promote` before hot-reload | ☐ | P6.5 |
| T5.6 | First real run: 10 generations, width 7, idle window | ☐ | P6.6 |

## E6 Routing and learning

Issue #37 Phase 2. Start with deterministic pheromone thresholds; the GNN only
once the ledger holds enough tasks to train on.

| ID | Task | Status | Evidence / log |
|---|---|---|---|
| T6.1 | Experience graph: deposit wiring, trace query, synergy aggregation | 🟡 | §14.12, §14.13 Steps 1–3; substrate exists (`observations`, `experience`, `synergy`) |
| T6.2 | Heterogeneous observation graph + link predictor (RGCN/GraphSAGE) | ☐ | P7.1 (§19.4) |
| T6.3 | Cold start `W_proj` from frozen LLM hidden states | ☐ | P7.2 |
| T6.4 | `MODEL_WORLD` role (`Qwen-AgentWorld-35B-A3B`), surprise signal | ☐ | P7.3 — only item allowed to add hardware |
| T6.5 | Probes: `THEORIZE → PROBE → VERIFY`, `<2 s`, no side effects | ☐ | P7.4 |
| T6.6 | Role LoRAs: trajectory export, training, Fisher-Merge, pinning | ☐ | P7.5 |

## E7 Model intelligence

Prompt- and weight-level learning that is already measured.

| ID | Task | Status | Evidence / log |
|---|---|---|---|
| T7.1 | GEPA prompt evolution (mutation + Pareto crossover, live driver) | ✅ | §15.3 item 4 — frontier covers 3/3 seed failures; multi-rep validation open |
| T7.2 | Arm D adversarial refinement mesh | 🗑 | §15.3 item 3 — 33/40 @ ~4800 tok, fails the cost gate; mesh stays supervisor-of-one |
| T7.3 | ACE experience store (append-only bullets, guarded merge) | ✅ | §15.3 item 5 |
| T7.4 | Parametric memory: LoRA + EWC, trajectory export per role | 🟡 | §19.1, P2b — external `~/ki-station` training |
| T7.5 | Parametric-memory counter-position (MAAI C2 critique) | ⏸ | §P2b counter-position |

## E8 Operator surfaces

Telegram, console, Hermes plugin.

| ID | Task | Status | Evidence / log |
|---|---|---|---|
| T8.1 | Inline keyboards for gates and control actions | ✅ | §14.11 P0.1 |
| T8.2 | Short task IDs (unambiguous prefixes) | ✅ | §14.11 P0.2 |
| T8.3 | `/status` one-shot digest | ✅ | §14.11 P0.3 |
| T8.4 | Failure-first notifications with cause + retry buttons | ✅ | §14.11 P0.4 |
| T8.5 | Quiet event stream, verbose override | ✅ | §14.11 P1.8 |
| T8.6 | Model-suggestion flow on failure | ✅ | §14.11 P2.9 |
| T8.7 | Console v2: live view + problem surface | ☐ | §14.10 (deferred) |
| T8.8 | Hermes plugin as second approval surface | ☐ | §14.9 |

## E9 Fleet and model serving

Per-node llauncher, monitors, the model index.

| ID | Task | Status | Evidence / log |
|---|---|---|---|
| T9.1 | Per-node llauncher instead of Ollama-in-Docker | ✅ | §15.4.4, §18.3 |
| T9.2 | Monitor MCP tools | ✅ | §18.1 |
| T9.3 | Throughput verdict | ✅ | §18.2 |
| T9.4 | `~/ai_models/INDEX.md` model/server index | 🟡 | created 2026-11-25; node-c entry open (mesh task `1a212916ac96…`) |
| T9.5 | node-c: update to HEAD + Inkling-2-Mini iGPU evaluation | 🟡 | mesh tasks `1a212915d13e…`, `1a2129169b3b…` |
| T9.6 | LAN model sharing and demand planning | ☐ | §14.7 |

## E10 Distribution and docs

| ID | Task | Status | Evidence / log |
|---|---|---|---|
| T10.1 | Ship-gate V2 | ✅ | §16.2 |
| T10.2 | Distribution and deployment | ☐ | §14.8 |
| T10.3 | Docs cleanup, design notes absorbed | ✅ | §16 |
| T10.4 | AutoSwarm outlook (heterogeneous harnesses under one roof) | ⏸ | §14.5 |
| T10.5 | Not-pursuing list | 🗑 | §"Not pursuing" |

## Sequencing

1. **E4** — T4.1, T4.2, T4.3, T4.4 in that order; each ships ledger event +
   Telegram card + console rendering + one kill-surviving test (§19.5).
2. **E5** — only after #35 criteria 1–3 pass live. T5.1 and T5.2 first; T5.6 is
   the acceptance run.
3. **E6** — T6.1 close-out, then T6.2 only when the ledger holds enough tasks;
   T6.4 decides node placement before any timeline is promised.
4. **Parallel, cheap:** T9.4/T9.5 (fleet bookkeeping), T8.7, T1.6.

## Epic → log map

| Epic | Log sections |
|---|---|
| E1 | Built, §14, §14.4, P0–P2a, §15.4.2, §15.4.3, §15.4.6 |
| E2 | §14.14, §15.4, §15.4.1, §16.5 |
| E3 | §14.15.1–§14.15.12 |
| E4 | §19.0–§19.2 |
| E5 | §19.3 |
| E6 | §14.12, §14.13, §19.4 |
| E7 | §15.3, P2b, §16.3 |
| E8 | §14.9–§14.11 |
| E9 | §14.7, §15.4.4, §18 |
| E10 | §14.5, §14.8, §16 |
