"""T2: model_manager — llauncher MCP client + health collection.

The model_manager is the thin per-node process that owns serving control:
it talks MCP (stdio, newline-delimited JSON-RPC) to llauncher and reports
live NodeHealth. Hermetic tests drive a fake MCP server over a real pipe;
only collect_health touches real sysfs (monkeypatched).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from memnc.config import Settings
from memnc.model_manager import LLauncherMCP, ModelManager
from memnc.schema import NodeHealth

FAKE_SERVER = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        req = json.loads(line)
        if req.get("method") == "initialize":
            resp = {"jsonrpc": "2.0", "id": req["id"],
                    "result": {"serverInfo": {"name": "fake"}}}
        elif req.get("method") == "tools/call":
            args = req["params"]["arguments"]
            resp = {"jsonrpc": "2.0", "id": req["id"],
                    "result": {"content": [{"type": "text",
                                            "text": json.dumps(
                                                {"echo": args["model"]})}]}}
        else:
            resp = {"jsonrpc": "2.0", "id": req.get("id"),
                    "result": {}}
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    """
)


@pytest.fixture()
def fake_mcp():
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", FAKE_SERVER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    client = LLauncherMCP(proc.stdin, proc.stdout)
    yield client
    client.close()


def test_mcp_initialize_and_call(fake_mcp):
    info = fake_mcp.initialize()
    assert info["serverInfo"]["name"] == "fake"
    out = fake_mcp.call("swap_server", {"model": "qwen2.5:7b"})
    assert out == {"echo": "qwen2.5:7b"}


def test_cmd_expands_tilde_and_env():
    s = Settings(llauncher_mcp_cmd="~/bin/llp -m llauncher.mcp_server")
    argv = s.mcp_argv()
    assert argv[0].startswith("/")
    assert argv[0].endswith("/llp")


def test_collect_health_from_probe_and_ps(monkeypatch):
    monkeypatch.setattr(
        "memnc.model_manager.probe_vram",
        lambda: (1_500_000_000, 16_000_000_000),
    )
    monkeypatch.setattr(
        "memnc.model_manager.fetch_loaded_models",
        lambda base_url: [
            {"model": "qwen2.5:7b", "vram_mb": 5120.0},
            {"model": "qwen2.5-coder:7b", "vram_mb": 4800.0},
        ],
    )
    mgr = ModelManager(node="node-a", base_url="http://127.0.0.1:11434")
    h = mgr.collect_health(queue_depth=2)
    assert isinstance(h, NodeHealth)
    assert h.node == "node-a"
    assert h.gpu.vram_total_mb == pytest.approx(16_000_000_000 / 2**20, rel=1e-3)
    assert h.gpu.vram_free_mb == pytest.approx(
        (16_000_000_000 - 1_500_000_000) / 2**20, rel=1e-3
    )
    assert set(h.models) == {"qwen2.5:7b", "qwen2.5-coder:7b"}
    assert all(e.loaded for e in h.models.values())
    assert h.queue_depth == 2


def test_collect_health_survives_probe_crash(monkeypatch):
    def boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr("memnc.model_manager.probe_vram", boom)
    monkeypatch.setattr(
        "memnc.model_manager.fetch_loaded_models", lambda base_url: []
    )
    h = ModelManager(node="node-a", base_url="http://x").collect_health()
    assert h.gpu.vram_free_mb == 0.0 and h.gpu.vram_total_mb == 0.0
    assert h.models == {}
