# Konzept: Hermes ↔ memnc-Mesh

> Stand: 2026-11-20 · Autor: Hermes · Status: zum Nachdenken (Frank)

## Kernidee

Hermes ist **kein** Mesh-Worker. Es ist der **Supervisor** — die Intelligenz,
die Aufgaben dekomponiert, delegiert, Ergebnisse validiert und entscheidet.
Das Mesh bleibt die **Ressourcen-Schicht** (Modelle, VRAM, Nodes).

Zwei Verbindungen:

```
┌─────────────────────────────────────────────────┐
│  HERMES (Supervisor)                            │
│  - Task Decomposition                           │
│  - Subagent Orchestration                       │
│  - Ergebnis-Validierung                         │
│  - Mensch-Kontakt (Telegram)                    │
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

## [1] AgentAdapter — Hermes als Mesh-Agent

**Was existiert:** `src/memnc/adapter.py` (M7, fertig). Generischer NATS-Adapter:
- `advertise(skill)` → Capability ins Registry-KV
- `_work_loop(subject)` → Request/Reply auf `mem.agent.<id>.work`
- `_heartbeat_loop` → Liveness + TTL-Refresh

**Was fehlt für Hermes:** Ein konkreter **Handler**, der Mesh-Aufgaben in
Hermes-Tasks übersetzt:

```python
class HermesAdapter(AgentAdapter):
    def handler(self, payload: dict) -> dict:
        # payload: task, context, plan, role, model_override
        # → delegiere an Hermes (CLI: hermes run --task "..." oder API)
        # → Hermes dekomponiert, delegiert an Subagenten, validiert
        # → Ergebnis zurück als JSON
        ...
```

**Flow:**
1. Orchestrator dispatcht Rolle (z.B. `ROLE_CODING`)
2. Adapter empfängt auf `mem.agent.hermes-supervisor.work`
3. Adapter ruft Hermes auf
4. Hermes dekomponiert, delegiert, validiert
5. Ergebnis geht zurück ins Mesh
6. Orchestrator prüft: Task fertig?

**Identity & ACL (M9):**
- `agent_id = "hermes-supervisor"`
- **CHAIN-Grant** vom Orchestrator (nicht AUTO — deny-by-default)
- Nur auf eigenen pinned Inbox antworten
- Kein Zugriff auf fremde Inboxes

**Skills:** `orchestration`, `code`, `research`, `planning`

## [2] Live-State Bridge — Mesh → Hermes (Read-only)

Hermes braucht Echtzeit-Einblick ins Mesh, ohne es zu steuern.

**Option A (empfohlen): NATS-Subscription im Hermes-Plugin**
- `plugins/memnc/` subscribt `mesh.events.*` + `mesh.router.scores`
- Pusht Events in Hermes-Session
- Frank fragt "Was läuft im Mesh?" → Hermes antwortet aus Live-State
- Kein Extra-Prozess, Gerüst schon da

**Option B: HTTP Sidecar**
- Kleiner Service liest NATS, serviert REST/SSE auf `:8480`
- Hermes ruft via curl ab
- Entkoppelt, aber Extra-Prozess

## Was Hermes NICHT macht

| ❌ | Warum |
|---|---|
| Mesh-Worker | Hat kein eigenes Modell; ist Supervisor |
| Registry schreiben (ohne Grant) | M9: deny-by-default |
| Fremde Inboxes adressieren | M9: pinned inbox |
| Router-Entscheidungen | M8: Orchestrator bleibt routing-authoritative |
| Node-Power-Steuerung | T5: bleibt am Node-Controller |

## Implementierungsschritte (Reihenfolge)

1. `HermesAdapter` in `plugins/memnc/` — konkreter Handler
2. Grant-Bootstrap — Orchestrator erteilt CHAIN-Grant
3. Events-Subscription — Plugin subscribt `mesh.events.*`
4. Test: Mesh dispatcht Task → Hermes antwortet → Mesh bestätigt

## Offene Fragen

- Wie ruft Hermes-Adapter Hermes auf? (CLI vs. API vs. direkt im Prozess?)
- Grant-Bootstrap: einmalig manuell oder automatisch beim ersten Heartbeat?
- Soll Hermes auch **Claims** stellen können (M11 Referee-Modus)?
- Fehlerfall: Hermes timeout → Mesh retry? Wer entscheidet?
