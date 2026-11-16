# Model-Management + Digital Twin + NATS-Modelltransport (Implementierungsplan)

> **For Hermes:** Umsetzung taskweise (TDD), Commit nach jedem Task. Konzept-Grundlage: `docs/model-management.md` (Milestones MM1–MM5).

**Goal:** Weg von der VRAM-Blockade-Kette und Blind-Routing: pro Node ein Model-Manager (Reuse: llauncher-MCP), der den Mesh-Twin füttert und Load/Unload/Drain-Intents ausführt; Planner allokiert pro Aufgabe; GGUFs wandern per NATS JetStream Object Store zwischen Nodes.

**Architecture:** Kein Kubernetes, keine neue Serving-Software. llauncher (bereits auf Hermes-Host, node-a, maco/180) bleibt die Kontroll-Ebene pro Node; memnc bekommt einen dünnen `model_manager`-Prozess, der llauncher via MCP-Client (stdio) abfragt und mit NATS verdrahtet (Health-Announce raus, Intents rein). Der Orchestrator wird zum puren Executor; der Planner liest den Twin (`mesh.resources.view`) und reserviert per `mesh.alloc.request`.

**Tech-Stack:** memnc (pydantic-Settings, nats-py 2.16), llauncher-MCP (`mcp` stdio client), JetStream KV + Object Store, systemd-User-Units, Docker Compose unverändert.

---

## Reuse-Entscheidung (warum llauncher, nicht llama-swap)

| Aufgabe | gelöst von | Reuse |
|---|---|---|
| Modell starten/stoppen/swappen pro Node | llauncher `start/stop/swap_server`, `start/stop_backend` | ✓ existiert |
| Live-Telemetrie | `server_status`, `server_slots`, `server_metrics`, `backend_status` | ✓ existiert |
| VRAM-Pre-Flight | llauncher `validate_models` (GPUHealthCollector, ROCm) | ✓ existiert |
| Auditierbarkeit | `read_audit` (ADR-8), `get_server_logs` | ✓ existiert |
| Gesundheitszustand → Mesh | **NEU:** `model_manager` (dieser Plan) | bauen |
| Twin-Aggregation + Allokation | **NEU:** Orchestrator/Planner (dieser Plan) | bauen |
| GGUF-Transport zwischen Nodes | **NEU:** JetStream Object Store (dieser Plan) | bauen |

llama-swap ist entwertet (Praxistest, Frank: unzuverlässig) — nur noch als konzeptionelle Referenz für groups/ttl/swap-Semantik im Doc. Ollama bleibt nur als Shim/Registry-Quelle, nicht mehr als kontrollierte Serving-Schicht; die VRAM-Blockade (`qwen2.5-coder:32b` + 31B-Gemma auf 16 GB) wird durch llauncher-Lifecycle + Twin gelöst, nicht durch Model-Downgrades.

## Subjects / Datenmodell (K8s-kompatibles Vokabular)

```
mesh.node.health            publish  {node, ts, gpu:{vram_free_mb, vram_total_mb, temp_c},
                                      models:{<id>:{loaded, vram_mb, load_s, tok_s, roles}},
                                      queue_depth, serving:"llauncher"}
mesh.resources.view         request  -> Capability-Matrix aller Nodes (Twin, staleness >30 s = tot)
mesh.alloc.request          request  {model, task_id} -> {node, ok, reason}   (Claim, erster gewinnt)
mesh.model.intent           request  {op: load|unload|drain|ensure, model, node} -> result
mesh.model.transport.*      Object Store "gguf" + KV "model-manifest" {sha256, size, chunks}
```

KV-Buckets: neu `MODEL_MATRIX` (Twin-Snapshot, ephemeral TTL), bestehend: `ROSTER_BUCKET` (T6, unverändert).

## Wichtige Randbedingungen

- **Henne-Ei gelöst per Fact #113:** Mesh/Planner äussern nur Bedarf (Intent), Ausführung trennt — kein Modell-Swap aus dem Inferenzpfad heraus.
- NATS vom Host: `NATS_URL=nats://127.0.0.1:4222`; im Compose-Netz `nats://nats:4222`. NKey-Seeds in `secrets/` (gitignored).
- `systemctl --user` braucht `XDG_RUNTIME_DIR` + `DBUS_SESSION_BUS_ADDRESS` (llauncher-Regel 1) — der model_manager läuft als systemd-User-Unit, nicht im Container, damit er auf llauncher + GPU zugreifen kann.
- VRAM-Read direkt: `/sys/class/drm/cardN/device/mem_info_vram_used` (AMD) als Fallback neben llauncher-Metriken.
- Tests ruff-clean, Pre-Commit = volle Suite, Commits nur grün.

---

## Tasks

### T0 — Housekeeping (Voraussetzung)
- H4-Dateien committen: `scripts/gen_nkeys.py`, `docker-compose*.yml`, `tests/test_nkeys.py`, `.env`, `docs/model-management.md` (Suite muss grün sein; `scripts/check_no_personal.py` Gate).
- Bug-Fix Quick-Win: Orchestrator-Log „experience record failed: Read-only filesystem 'data'" → `docker-compose.yml` Volume `data/` rw. Test: Container neu, Task laufen lassen, Log ohne den Fehler.

### T1 — Schema + Konstanten
**Files:** `src/memnc/schema.py` (neue Models: `NodeHealth`, `ModelEntry`, `AllocRequest`, `AllocResult`, `ModelIntent`), `src/memnc/config.py` (`LLAUNCHER_MCP_CMD`, `NODE_NAME` existiert, `TWIN_STALE_S=30`, `HEALTH_INTERVAL_S=5`).
**Test:** `tests/test_schema_twin.py` — roundtrip JSON der Health-Message, Default-Werte.

### T2 — model_manager Kern (llauncher-MCP-Client)
**Files:** Create `src/memnc/model_manager.py`; Test `tests/test_model_manager.py`.
- MCP-stdio-Client auf llauncher (`mcp.ClientSession`, venv-Pfad aus Config).
- `collect_health()` → `backend_status` + `list_models` + SysFS-VRAM → `NodeHealth`.
- Health-Loop: publish `mesh.node.health` alle `HEALTH_INTERVAL_S` (Request/Reply-Fähigkeit: auch auf Request antworten, nicht nur publishen).
- TDD: collect_health mit fake-MCP-Session; Stale-Detection (`is_stale(ts, now, TWIN_STALE_S)`).

### T3 — Twin-Aggregation im Orchestrator
**Files:** Modify `src/memnc/orchestrator.py` (Subscriber auf `mesh.node.health` → KV `MODEL_MATRIX`, Key = Node); `src/memnc/bus.py` (`ensure_model_matrix()` analog `ensure_roster()`).
**Test:** `tests/test_twin.py` — 3 Nodes announacen, View liefert Matrix, stale Node ausgeschieden; Request/Reply `mesh.resources.view`.

### T4 — Intents (load/unload/drain/ensure)
**Files:** `src/memnc/model_manager.py` (Intent-Loop: subscribe `mesh.model.intent`, Request/Reply); Mapping auf llauncher-Tools: `load→start_server`, `unload→stop_server`, `drain→stop_server für alle Modelle der Node` (liefert VRAM-frei), `ensure→validate_models + start_server`.
- Drain ist der offizielle Ersatz für die Blockade-Kette: Vor einem großen Load drain't die Node die anderen Modelle, statt blinden Timeouts.
**Test:** `tests/test_model_intents.py` — fake-MCP: Intent load → start_server gecallt, Result ok; unbekanntes Modell → error-Envelope; drain → alle gestoppt.

### T5 — Planner-Allokation
**Files:** Modify `src/memnc/orchestrator.py` (`_maybe_request_model`, `_dispatch`): vor Dispatch `mesh.alloc.request` (Claim KV `alloc/<task_id>` → node, erster Claim gewinnt); Ergebnis-JSON `{"model","node","reason","fallback"}` aus Planner-Tool-Call.
- `mesh.alloc.request`-Server im Orchestrator: prüft Twin (VRAM frei vs. Modellgröße, schon geladen → Ladezeit 0), bucht Claim, stösst Intent an der Ziel-Node.
**Test:** `tests/test_alloc.py` — zwei Nodes, Modell passt nur auf einen → richtiges Claim; Modell nirgends → `ok:false, reason:"no_node"`; Doppel-Claim → zweiter reallokiert.

### T6 — NATS-Modelltransport (GGUF via JetStream Object Store)
**Files:** Create `src/memnc/model_transport.py`; Test `tests/test_model_transport.py`.
- Object Store `gguf`; KV `model-manifest`: `{name, sha256, size_mb, node_sources[]}`.
- `offer_model(model_path)`: chunked Upload (morsel ~1 MiB), Manifest publish.
- `fetch_model(manifest)`: Download in `~/.memnc/models/`, sha256-verify.
- Anbindung an `ensure`-Intent: Modell nicht lokal → Manifest lookup → fetch → llauncher `list_models`/Config aktualisieren → start.
- Pipeline für erste Version: 16-GB-Karte, GGUFs 5–20 GB → object store chunked streaming ist ok (LAN).
**Test:** Unit mit embedded NATS-mock oder gegen laufenden NATS-Container (wie bestehende Tests); roundtrip sha256-identisch.

### T7 — Werkstatt-Integration: Orchestrator-Dispatch benutzt Allokation
**Files:** Modify `src/memnc/orchestrator.py` `_dispatch`/`_fanout` (work-Message bekommt `alloc: {node}`); Modify `src/memnc/worker.py` (Worker akzeptiert Task nur wenn `node` matcht oder Modell lokal geladen — „kannst du gerade wirklich?" als Gate).
**Test:** `tests/test_dispatch_alloc.py` — Task mit Claim auf node-a → landet nur dort.

### T8 — Rollout + Compose
- `docker-compose.yml`: Orchestrator + Worker-Container behalten NATS; model_manager als **systemd-User-Unit** pro Node (`scripts/systemd/memnc-model-manager.service`, Vorlage vorhanden in `scripts/systemd/`).
- node-a + maco/180 erst nach Hermes-Host-Erfolg (Roster-Konsistenz prüfen — Issue #5).

### T9 — E2E-Beweis
- `scripts/e2e.sh` erweitern: Task absetzen → Logs zeigen (1) Health-Announces, (2) Alloc-Claim, (3) ggf. Intent load/drain mit Ladezeit, (4) DONE ohne Planner-Timeout.
- Erfolgs-Kriterium: Task auf 16-GB-Karte läuft in einem Anlauf (<2 min), kein `already_empty`/VRAM-Timeout.

---

## Risks & Tradeoffs
- **llauncher-MCP-Versionen pro Node abweichend** (Skill: mcp==1.2.0 pinned, shadowed venv copies!) → model_manager mustert Version ab / fehlschlagen mit klarer Meldung; `pip install -e .` Fix dokumentieren.
- **Registry ≠ Live-Reality** (llauncher-Regel 7): Health nie aus Registry-JSON, immer aus `backend_status`/`server_metrics` + SysFS.
- **GGUF über NATS:** 20-GB-Datei = ~20k Chunks; erste Version nur LAN + Hash-verify. Alternative später: HTTP-Download mit NATS nur als Manifest (Pointer), falls Throughput enttäuscht.
- **Rückwärtskompatibilität:** Rollout pro Node; ohne Health-Announce fällt Node aus der Allokation raus (default: eigenes Node-Allokation wie heute, kein Hard-Brake).
- **Fisher/EWC-Training-Workloads** (~/ki-station) nicht Teil dieses Plans — VRAM-Konkurrenz bleibt manuell, Twin macht sie aber sichtbar.

## Offene Fragen (an Frank)
1. 180 (iGPU 780M, 64 GB shared): llauncher dort nochmal verifizieren (pp. VRAM-frei aus SysFS oder rocm-smi nicht nutzbar auf iGPU → ggf. `amdgpu_top`-Pfad)?
2. GGUF-Quelle fürs Object Store: repliziert ihr GGUFs zentral vom Hermes-Host, oder lädt jede Node selbst von HF?
3. Allokations-Politik v1: „schon geladen > passt frei > drain-größtes" — ok als erste Regel?
