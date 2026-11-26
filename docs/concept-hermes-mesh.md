# Concept: Hermes ↔ memnc Mesh

> Status: for consideration

## Core Idea

Hermes is **not** a mesh worker. It is the **supervisor** — the intelligence that
decomposes tasks, delegates, validates results and decides. The mesh remains the
**resource layer** (models, VRAM, nodes).

Two connections:

```
┌─────────────────────────────────────────────────┐
│  HERMES (Supervisor)                            │
│  - Task Decomposition                           │
│  - Subagent Orchestration                       │
│  - Result Validation                            │
│  - Human contact (Telegram)                     │
└──────────┬──────────────────────┬──────────────┘
           │                      │
     [1] NATS Bus            [2] Events
     (AgentAdapter)         (Read-only)
           │                      │
┌──────────▼──────────────────────▼──────────────┐
│  memnc MESH                                   │
│  - NATS JetStream (tasks, registry, events)   │
│  - Orchestrator (routing, dispatch, ACL)      │
│  - Nodes: RX 7600 XT, maco, node-a            │
│  - Models: GGUF, Ollama, llama-server         │
└─────────────────────────────────────────────────┘
```

## [1] AgentAdapter — Hermes as Mesh Agent

**What exists:** `src/memnc/adapter.py` (M7, done). Generic NATS adapter:
- `advertise(skill)` → capability into the registry KV
- `_work_loop(subject)` → request/reply on `mem.agent.<id>.work`
- `_heartbeat_loop` → liveness + TTL refresh

**What is missing for Hermes:** a concrete **handler** that translates mesh tasks into
Hermes tasks:

```python
class HermesAdapter(AgentAdapter):
    def handler(self, payload: dict) -> dict:
        # payload: task, context, plan, role, model_override
        # → delegate to Hermes (CLI: hermes run --task "..." or API)
        # → Hermes decomposes, delegates to subagents, validates
        # → result back as JSON
        ...
```

**Flow:**
1. Orchestrator dispatches a role (e.g. `ROLE_CODING`)
2. Adapter receives on `mem.agent.hermes-supervisor.work`
3. Adapter calls Hermes
4. Hermes decomposes, delegates, validates
5. Result goes back into the mesh
6. Orchestrator checks: task done?

**Identity & ACL (M9):**
- `agent_id = "hermes-supervisor"`
- **CHAIN grant** from the orchestrator (not AUTO — deny by default)
- Only reply on the own pinned inbox
- No access to foreign inboxes

**Skills:** `orchestration`, `code`, `research`, `planning`

## [2] Live-State Bridge — Mesh → Hermes (Read-only)

Hermes needs real-time insight into the mesh without controlling it.

**Option A (recommended): NATS subscription in the Hermes plugin**
- `plugins/memnc/` subscribes to `mesh.events.*` + `mesh.router.scores`
- Pushes events into the Hermes session
- The owner asks "What is running in the mesh?" → Hermes answers from live state
- No extra process, the scaffolding already exists

**Option B: HTTP sidecar**
- A small service reads NATS and serves REST/SSE on `:8480`
- Hermes fetches via curl
- Decoupled, but an extra process

## What Hermes Does NOT Do

| ❌ | Why |
|---|---|
| Mesh worker | Has no own model; is the supervisor |
| Write to the registry (without grant) | M9: deny by default |
| Address foreign inboxes | M9: pinned inbox |
| Router decisions | M8: orchestrator remains routing-authoritative |
| Node power control | T5: stays with the node controller |

## Implementation Steps (Order)

1. `HermesAdapter` in `plugins/memnc/` — concrete handler
2. Grant bootstrap — orchestrator issues the CHAIN grant
3. Events subscription — plugin subscribes to `mesh.events.*`
4. Test: mesh dispatches task → Hermes responds → mesh confirms

## Open Questions

- How does the Hermes adapter call Hermes? (CLI vs. API vs. directly in the process?)
- Grant bootstrap: one-time manual step, or automatic at the first heartbeat?
- Should Hermes also be able to place **claims** (M11 referee mode)?
- Failure case: Hermes timeout → mesh retry? Who decides?
