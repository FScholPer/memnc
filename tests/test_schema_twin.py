"""T1: digital-twin wire schema (NodeHealth et al.) + config constants.

The model_manager publishes NodeHealth on mesh.node.health; the orchestrator
aggregates it into the MODEL_MATRIX KV (T3). Allocation (T5) speaks
AllocRequest/AllocResult; intents (T4) speak ModelIntent. These tests pin the
JSON round-trips and defaults so every producer/consumer stays compatible.
"""

from memnc.config import Settings
from memnc.schema import (
    AllocRequest,
    AllocResult,
    ModelEntry,
    ModelIntent,
    NodeHealth,
)


def test_nodehealth_roundtrip_defaults():
    h = NodeHealth(node="node-a")
    d = h.model_dump_json()
    h2 = NodeHealth.model_validate_json(d)
    assert h2.node == "node-a"
    assert h2.gpu.vram_free_mb == 0.0
    assert h2.gpu.vram_total_mb == 0.0
    assert h2.models == {}
    assert h2.queue_depth == 0
    assert h2.serving == "llauncher"
    assert h2.ts  # stamped at construction


def test_nodehealth_full_payload_roundtrip():
    h = NodeHealth(
        node="node-c",
        gpu={"vram_free_mb": 8192.0, "vram_total_mb": 16384.0, "temp_c": 61.0},
        models={
            "qwen2.5:7b": ModelEntry(
                loaded=True, vram_mb=5120.0, load_s=14.2, tok_s=38.5, roles=["planner"]
            )
        },
        queue_depth=2,
    )
    h2 = NodeHealth.model_validate_json(h.model_dump_json())
    assert h2.gpu.temp_c == 61.0
    entry = h2.models["qwen2.5:7b"]
    assert entry.loaded and entry.vram_mb == 5120.0 and entry.roles == ["planner"]


def test_model_intent_ops():
    for op in ("load", "unload", "drain", "ensure"):
        i = ModelIntent(op=op, model="qwen2.5:7b", node="node-a")
        assert ModelIntent.model_validate_json(i.model_dump_json()).op == op


def test_alloc_request_result_roundtrip():
    r = AllocRequest(model="qwen2.5:7b", task_id="t1")
    r2 = AllocRequest.model_validate_json(r.model_dump_json())
    assert r2.model == "qwen2.5:7b"
    res = AllocResult(
        ok=True, node="node-a", model="qwen2.5:7b", reason="loaded", fallback=None
    )
    res2 = AllocResult.model_validate_json(res.model_dump_json())
    assert res2.ok and res2.node == "node-a"
    neg = AllocResult(ok=False, node=None, model="x", reason="no_node", fallback=None)
    assert AllocResult.model_validate_json(neg.model_dump_json()).ok is False


def test_twin_config_constants():
    s = Settings()
    assert s.twin_stale_s == 30
    assert s.health_interval_s == 5
    assert s.llauncher_mcp_cmd  # non-empty default command line
