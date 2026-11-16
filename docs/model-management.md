# Konzept: Model-Management-Schicht + Node-Digital-Twin

Stand: 2026-11-16 · Status: Entwurf (Feedback von Frank einarbeiten)

## Problem

- Mesh-Tasks scheitern, weil der Orchestrator blind routet: Er kennt weder VRAM-Auslastung,
  noch welche Modelle gerade geladen sind, noch Ladezeiten.
- Konkreter Fall: 31B-Critic saß im VRAM, Planner (7B) musste neu laden → Timeout.
- Ollama lädt on-demand und vergisst nach ~5 min (`keep_alive`) — beides unkoordiniert.
- Es gibt keine Schicht, die Modelle pro Node verwaltet und dynamisch lädt/entlädt.

## Recherche-Ergebnisse (Prior Art)

### llama-swap (mostlygeek/llama-swap, aktiv, Golang, Single-Binary)
- Transparenter Proxy vor beliebigen OpenAI-kompatiblen Servern (llama.cpp, vLLM, tabbyAPI).
- **On-demand model switching**: Request mit `model=X` → lädt X, swapped den falschen
  Upstream raus. Genau das, was wir brauchen — ohne eigenes Daemon-Management.
- **`/running`** — Liste aktuell geladener Modelle → **Digital-Twin-Quelle**.
- **`/unload`** — manuelles Entladen (per Modell-ID oder alle).
- **`ttl`** — automatisches Entladen nach Timeout (pro Modell konfigurierbar).
- **`groups`** — mehrere Modelle gleichzeitig; `swap`/`exclusive`/`persistent`:
  - `swap: true` → nur ein Modell der Gruppe im VRAM
  - `exclusive: true` → entlädt andere Gruppen beim Start
  - `persistent: true` → bleibt immer geladen (z.B. Planner klein halten)
- **`aliases`**, `env`, `cmdStop` (Docker/Podman), `${PORT}` dynamische Ports,
  `healthCheckTimeout`, `hooks` (Preload beim Start).
- Konfiguration: eine einzige `config.yaml`.
- **⚠ Entwertet durch Praxistest (Frank): in der eigenen Nutzung nicht
  zuverlässig gewesen → NICHT als Serving-Schicht wählen.** Bleibt hier nur
  als konzeptionelle Referenz (groups/ttl/swap-Semantik).

### llauncher (Frank's eigene Infrastruktur — PRIMÄR-Option)
- **MCP-Konzept ist agent-nativ** (Frank: „Das coole an llauncher ist das
  MCP-Konzept") — Registry-Truth als MCP-Resource, Agenten fragen Zustand ab,
  statt self-loading in Ollama. Genau der Fix fürs „Agent lädt blind selbst".
- Vollständige Kontroll-Ebene als MCP-Tools: `start_server`, `stop_server`,
  `swap_server`, `cancel_server`, `server_status`, `server_slots`,
  `server_metrics`, `get/set_model_config`, `list_backends`/`list_models`,
  `validate_models` (Weights-Prüfung), `backend_status` (systemd + Port +
  Health), `get_server_logs`, `read_audit` (ADR-8 Audit-Log).
- llama.cpp-basiert (`llama-server`), systemd-User-Units, Port-Management,
  Slots inkl. Prompt-Text, Live-Inferenz-Telemetrie.
- **Was fehlt (MM-Aufgabe):** NATS-Brücke — Health-Announce + `load/unload/
  drain`-Intents von NATS auf MCP-Aufrufe mappen. Dann ist llauncher der
  Model-Manager selbst, kein extra Daemon nötig.
- **Bewertung ehrlich nachgeholt (Korrektur):** vorher unzureichend behandelt —
  Agent lud stattdessen selbst Modelle in Ollama. llauncher deckt Lifecycle
  (start/stop/swap), Observability (status/slots/metrics/logs/audit) und
  Validierung ab — mehr als llama-swap, und erprobt auf Frank's Rig
  (RX 7600 XT / 780M / RX 6800 XT).

### vLLM (Referenz fürs Pool-Management)
- `--gpu-memory-utilization` = "claim max X% des VRAM" — zentrales Pool-Prinzip.
- Für große Modelle auf geteilten Karten: utilisation dynamisch anhand tatsächlich
  freiem VRAM berechnen (NVIDIA-Forum-Pattern mit nvidia-smi-Query beim Start).

### Latency-Aware Orchestration (arXiv 2609.03335)
- Prediction-guided Runtime für parallele Agent-Workflows auf heterogenen GPUs:
  Predictor schätzt Aktivierungs-/Ladekosten pro Modell × Device; Scheduler wählt
  Fusion/Lifecycle/Binding unter Live-Pool-State und replant aus Feedback.
- → Langfristige Richtung: statt hartem Routing ein Kostenmodell (Ladezeit + VRAM-Fit)
  pro Node/Modell-Paar, gelernt aus gemessenen Ladezeiten.

### „Kubernetes für Modelle" (DeepSeek-Recherche, 2026-11-16) — Vergleich

De-facto-Steuerungsebenen für KI-Workloads, sortiert nach Ansatz:
- **KServe** — K8s-Standard für Modell-Serving (Operator, vLLM/SGLang/Triton).
- **AIBrix** (ByteDance) — LLM-Inferenz-Steuerungsebene: routet nach Live-Zustand
  der Engines, Autoscaling nach Inferenz-Metriken, verwaltet Modelle + LoRA
  über OpenAI-API. Konzeptionell am nächsten an unserem Zielbild.
- **Kthena** — K8s-nativ, Prefill/Decode-Disaggregation, Zero-Downtime-Updates.
- **llm-d** — verteilter Inferenz-Stack; Routing-Intelligenz (KV-Cache-bewusst)
  kann inzwischen auch **ohne Kubernetes** laufen — einziger Kandidat mit
  standalone-Modus, aber schwerer Stack.
- **Atlas** — engine-agnostische Steuerungsebene + Router: Spezifikation →
  GPU-Kapazitätsprüfung → Routing auf einen OpenAI-Endpoint. Gut als Blaupause
  für unsere `mesh.alloc.request`-Semantik.
- **LLMKube** — Operator für heterogene Hardware (NVIDIA/AMD/Apple Silicon).
- **Taho / Slurm** — K8s-Alternative bzw. HPC-Batch-Klassiker; für uns irrelevant.

**Bewertung für memnc:** Alle K8s-nativen Setups setzen einen Cluster voraus —
wir haben 2–3 Homelab-Nodes mit NATS + Docker Compose; ein k3s-Cluster wäre
Infrastruktur-Overkill und Frank will explizit **kein Kubernetes bauen**. Aber
die Konzepte übernehmen wir direkt:
1. **AIBrix „Route by live engine state"** = unser Planner-Allokationsziel
   (Entscheidung aus Live-Twin statt statischem Roster).
2. **Atlas-Semantik** (Spec einreichen → Kapazität prüfen → routen) = Ablauf
   unseres `mesh.alloc.request`.
3. **AIBrix LoRA-Verwaltung** = später relevant fürs parametrische Memory
   (LoRA pro Wissensbereich laden/entladen statt Basemodelle zu swapen).
Unser Weg bleibt: schlanker eigener Twin auf NATS (MM1–MM5), keine K8s-Abhängigkeit.

### Multi-Host-Swarms & MCP-Orchestrierung (Recherche 2, 2026-11-16)

**Swarms/Clusters (Bottom-Up):**
- **mycoSwarm** — mDNS-Auto-Discovery, Capability-Austausch, Routing nach
  Hardware-Fähigkeit. Takeaway: **mDNS als Discovery-Erweiterung** für uns
  (Nodes melden sich selbst statt hardcoded Roster).
- **Mesh-LLM** — siehe eigene Sektion unten (Primär-Referenz).
- **Exo** — Apple-Silicon-Fokus, MLX-Backend; für maco/180 (780M) nur
  bedingt, aber Discovery-Muster gleich.

**llama.cpp-Cluster-Manager:**
- **Tightwad** — poolt CUDA+ROCm via llama.cpp RPC über Maschinen hinweg;
  Killer-Feature: **Speculative Decoding über Netzwerk** (kleines Modell
  draftet auf schwacher Maschine, großes verifiziert auf starker → 2-3×).
  Spannend für node-a (6800 XT) + Hermes-Host (7600 XT) Kombination, aber
  komplex; Idee merken, nicht jetzt.
- **RPC Manager** — Web-UI + mDNS-Discovery + **Live-Telemetrie (VRAM,
  Temperatur) pro Node**. Bestätigt: Telemetrie als First-Class-Twin-Daten.
- **BareMetalRT** — TensorRT-nativ, NVIDIA-only; irrelevant (AMD).

**MCP als Steuerungsebene (validiert unseren llauncher-Weg):**
- **apra-fleet** — „was K8s für Container war, für AI-Agenten": Maschinen per
  SSH als Fleet-Member, Routing nach Kosten+Fähigkeiten, Observability.
  Beweist: **MCP-kontrollierte Multi-Host-Orchestrierung ohne K8s ist
  productionfähig gedacht** — gleiche Architekturrichtung wie llauncher-MCP
  + NATS-Twin bei uns.
- **Myrmex Hive** — dezentrales Agent-Orchestrierungs-Framework auf MCP-Basis,
  **Zero-Inbound-Ports** (nur ausgehende Verbindungen). Exakt unser
  NATS-Subscriber-Pattern — Nodes verbinden sich zum Mesh heraus, keine
  offenen Ports.
- **Argus MCP** — Gateway, das mehrere MCP-Server hinter einem Endpoint
  aggregiert. Relevant, wenn der Planner auf llauncher-MCP mehrerer Nodes
  gleichzeitig zugreifen soll.

**Fazit:** Bestätigt unsere Architektur (MCP-Kontrolle + dezentraler Twin,
kein K8s). Neue Takeaways: (1) mDNS-Discovery statt hardcoded Roster, (2)
Telemetrie als First-Class-Twin-Felder, (3) Tightwad-SpecDec als Zukunftsoption.

### Mesh-LLM (Skippy + Mesh, 2026, Rust/QUIC — Referenz-Architektur)
Mesh-LLM zeigt, wie das Ganze produktiv aussieht, und liefert konkrete
Bausteine für unseren Planner-as-Allocator-Ansatz:

- **Zwei Ebenen getrennt**: Skippy (Model-Management, native Inference, OpenAI
  Serving) + Mesh (Discovery, Routing, Gossip). → Bei uns: Model-Manager
  (Node-Layer) + Orchestrator (Routing-Layer), NATS statt QUIC.
- **Runtime-Lifecycle-Intents** (exakt die Verben, die wir brauchen):
  `load-model` (one-shot), `ensure-model` (maintained present, mit Retry —
  survives Transient-Failures), `unload-model`, `drain-model` (in-flight work
  fertig, dann entladen). **drain-model ist der saubere Ersatz für unsere
  VRAM-Blockade-Kette**: schon laufende Requests nicht abwürgen.
- **`scan-refresh` / Inventory**: getNode-Zustand (config + geladene Modelle)
  als sortierte Inventory-Liste → unser Digital-Twin-Pendant. Owner-Control
  läuft über einen separaten Kontroll-Kanal (bei uns: NATS-Subject mit
  NKey-gescopeten Permissions — H4-Mechanik passt genau).
- **Dynamisches Capability-Wissen**: Nodes teilen sich per Gossip mit, welche
  Modelle sie gerade serve können; Routing entscheidet per `model`-Feld. Bei
  uns: `mesh.node.health` + Capability-Matrix (Modell × Node × Engine).
- **`benchmark tune`** misst Ladezeiten/Throughput pro Modell×Node×Settings und
  schreibt Empfehlungen zurück → Prior Art für unser MM5-Kostenmodell
  (Ladezeit-Budget statt nur VRAM-Fit).
- **Nachteil für uns**: Mesh-LLM ist ein komplettes Produkt (QUIC, Nostr-Discovery,
  eigenes Splits-Protokoll) — Migration würde unsere NATS/Mesh-Infrastruktur
  ersetzen. Wir übernehmen Konzepte (Intents, drain, Inventory, Benchmark),
  nicht das Produkt.

## Architektur-Entwurf

```
┌─────────────────────────────────────────────────────────┐
│ Orchestrator (Routing)                                  │
│  - Roster: welche Node kann welche Rolle/Modelle        │
│  - Digital Twin: pro Node {geladen, VRAM-frei, Queue}   │
│  - Routing-Regel: laden < schwelle? → Node mit Modell   │
│    geladen bevorzugen; sonst Node mit freiem VRAM       │
└──────────────┬──────────────────────────────────────────┘
               │ mesh.node.health (KV/NATS, periodisch)
┌──────────────▼──────────────────────────────────────────┐
│ Model-Manager (ein Container pro Node, neben Ollama)    │
│  - quellt llama-swap /running + nvidia-smi (VRAM)       │
│  - publiziert Health-Announce alle N Sekunden:          │
│    {node, models_loaded[], vram_free_mb, vram_total_mb, │
│     queue_depth, load_times{model:sec}}                 │
│  - nimmt Befehle entgegen: preload/unload/evict         │
└─────────────────────────────────────────────────────────┘
```

### Komponenten

1. **llama-swap als Serving-Schicht pro Node** (ersetzt Ollama-Raw-Access):
   - `config.yaml` pro Node: alle Mesh-Modelle mit `cmd` (llama-server oder
     vLLM), `ttl`, `groups`.
   - Group-Strategie auf 16-GB-Karte: `persistent` nur für das kleinste
     Planner-Modell; Coder/Critic in einer `swap: true`-Gruppe.
   - Vorteil: kein eigener Daemon-Code fürs Laden/Entladen — das macht
     llama-swap erprobt; wir bauen nur die Kontroll-Ebene.

2. **Model-Manager-Agent** (neu, `src/memnc/model_manager.py`):
   - Pollt `llama-swap /running` + `nvidia-smi` (VRAM free/total).
   - Publiziert `mesh.node.health` auf NATS (oder KV-Bucket `node.health`).
   - Endpunkte: `preload <model>` (Request an llama-swap, um den Upstream zu
     starten ohne Inference), `unload <model>`, `evict` (größtes Modell raus).
   - Misst Ladezeiten und schreibt sie in den Health-Announce → Basis für
     Routing-Kostenmodell.

3. **Digital Twin im Orchestrator**:
   - In-Memory-State pro Node: `{node, models_loaded, vram_free, vram_total,
     queue_depth, load_times, last_seen}`.
   - Aktualisierung: Health-Announces (push) + fehlen >30 s → Node als
     „verstorben" markieren.
   - Routing-Regel (v1, deterministisch):
     a) Node, auf dem das Zielmodell **schon geladen** ist → bevorzugen.
     b) Sonst Node mit **genug freiem VRAM** (Ladezeit inkl. Reserve).
     c) Sonst: `evict`-Befehl an die Node mit dem größten Residenten-Modell,
        dann (b).
   - Kein hartes Timeout mehr als Workaround — Ladezeiten fließen ins Budget.

4. **Ollama-Spezialfall (Übergang)**:
   - Ollama hat kein `/running`-API im llama-swap-Sinn, aber `/api/ps`
     (geladene Modelle + `size_vram`) und `/api/generate` mit `keep_alive: 0`
     zum Entladen. Model-Manager kann Ollama im interim-Modus quellen,
     bis llama-swap steht.

### Reihenfolge der Umsetzung (Milestones)

- **MM1** — Model-Manager liest `/api/ps` + VRAM, publiziert Health-Announce.
  (Digital Twin sichtbar, noch kein Routing-Verhalten.)
- **MM2** — Orchestrator sammelt Twin-State, loggt Routing-Entscheidungen mit
  Begründung („node-a: coder geladen, VRAM frei 9 GB → gewählt").
- **MM3** — Routing-Regel v1 aktiv (geladen > freier VRAM > evict).
- **MM4** — llama-swap als Serving-Schicht auf node-a, Ollama-Migration
  (config.yaml, groups, ttl); Model-Manager quellt dann `/running`.
- **MM5** — Kostenmodell: gemessene Ladezeiten pro Modell×Node → Routing
  berücksichtigt Ladezeit-Budget statt nur VRAM-Fit.

## Planner als Allocator (Zielbild, Frank-Vorgabe)

Der Planner bekommt **direkten Zugriff auf das Mesh** und allokiert pro Aufgabe
selbst — der Orchestrator wird zum puren Executor (Route + ausführen), die
Entscheidung „welches Modell auf welcher Node" trifft der Planner.

### Warum das geht
- Der Planner (7B) braucht als Output nur eine **strukturierte Allokation**:
  `{"model": "...", "node": "...", "reason": "...", "fallback": "..."}` —
  das ist ein Tool-Call, kein zusätzliches Wissensproblem.
- Das **dynamische Fähigkeits-Wissen** kommt aus dem Health-Announce-Stream:
  der Planner liest den Digital-Twin (Capability-Matrix + VRAM) als Teil seines
  Prompt-Kontexts — kompakt, pro Node eine Zeile.

### Datenmodell: Capability-Matrix (von den Nodes gemeldet, stetig aktuell)
```json
{
  "node": "node-a",
  "gpu": {"vram_free_mb": 12000, "vram_total_mb": 16384},
  "models": {
    "qwen2.5:7b":       {"loaded": true,  "vram_mb": 5100, "load_s": 6.2,  "tok_s": 14.1, "roles": ["planner"]},
    "qwen2.5-coder:32b":{"loaded": false, "vram_mb": 19800, "load_s": 95.0, "tok_s": 2.4, "roles": ["coder"], "cpu_offload": true},
    "gemma-31b":        {"loaded": false, "vram_mb": 14400, "load_s": 40.0, "tok_s": 5.0, "roles": ["critic"]}
  },
  "queue_depth": 0,
  "last_seen": "2026-11-16T17:32:00Z"
}
```
- `loaded`/`vram_free` ändern sich **stetig** → Health-Announce alle 5–10 s.
- `load_s`/`tok_s` werden beim Laden gemessen (Mesh-LLM-`benchmark tune`-Idee)
  → Kostenmodell wächst mit der Nutzung, kein manueller Pflegeaufwand.

### Ablauf pro Task (K8s-Scheduler-Pattern, pod-Infos als Vorbild)
1. **Task anfragen**: Planner bekommt Task + Task-Typ (plan/coding/critique).
2. **Twin lesen**: Planner ruft `mesh.resources.view` (Request/Reply auf NATS)
   auf → bekommt die aktuelle Capability-Matrix (K8s: `kubectl get pods -o wide`).
3. **Allokieren**: Planner wählt `model × node` nach Regel:
   - Modell passt in freien VRAM?
   - Schon geladen (Ladezeit 0) schlägt schnelleres aber zu ladendes Modell?
   - Ladezeit + Inferenzzeit vs. Task-Deadline?
   - Alternativen als `fallback` mitliefern, falls die Node stirbt.
4. **Reservieren**: Planner sendet `mesh.alloc.request` → Orchestrator bucht
   die Node (In-Memory-Claim, analog K8s-`nodeName` im Pod-Spec) und startet
   den Worker. Konfliktfall (zwei Planner gleichzeitig): erster Claim gewinnt,
   zweiter bekommt `409` und reallokiert.
5. **Ausführen + Feedback**: Nach dem Task meldet die Node die gemessenen
   `load_s`/`tok_s` zurück → Matrix aktualisiert sich (Self-Improving).

### Rollen-Shift
- **Orchestrator**: verwaltet Claims, führt aus, broadcastet Twin-State —
  kein Routing-Wissen mehr nötig.
- **Planner**: einziges Bauteil, das über Ressourcen entscheidet.
- **Model-Manager**: führt `load/unload/drain`-Intents aus (Mesh-LLM-Verben).

### Milestones (angepasst)
- **MM1** — Model-Manager: Health-Announce mit Capability-Matrix (obige Felder).
- **MM2** — Orchestrator: Twin-State sammeln, `mesh.resources.view`-Endpoint
  (Request/Reply), Twin-State sichtbar loggen.
- **MM3** — Planner: Mesh-Zugriff (NATS-User mit `mesh.resources.view`-Perm),
  Allokation als strukturierten Output, Orchestrator führt aus.
- **MM4** — llauncher als Serving/Kontroll-Ebene (NATS↔MCP-Brücke) +
  `drain`-Intents: VRAM-Blockade-Kette endgültig lösen.
- **MM5** — Kostenmodell aus gemessenen Ladezeiten (Feedback-Loop).

### Offene Fragen (für Frank)

- Sollen alle Nodes llama-swap bekommen, oder nur die mit GPU (node-a: RX 6800 XT)?
- Ollama behalten als Backend hinter llama-swap, oder direkt llama-server?
- Persistent-Strategie: Welches Modell soll „immer geladen" bleiben? (Vorschlag:
  kleinster Planner, ~5 GB — dann bleibt der Mesh-Task immer startfähig.)
- Priorität: erst MM1+MM2 (Observability) oder direkt MM3 (Routing-Verhalten)?
