"""T2: model_manager — llauncher MCP client + per-node health collection.

The model_manager is the thin per-node process that owns serving control:
it talks MCP (stdio, newline-delimited JSON-RPC) to llauncher and reports
live NodeHealth. The MCP client is minimal on purpose — initialize +
tools/call is all memnc needs (start/stop/swap, list_models,
validate_models, backend_status). Health collection reuses the T2
vram_probe numbers and Ollama /api/ps truth; it never crashes the node.
"""

from __future__ import annotations

import json
import sys
from typing import IO, Any

from memnc.schema import GpuHealth, ModelEntry, NodeHealth
from memnc.vram_probe import fetch_loaded_models, probe_vram  # noqa: F401

MCP_TIMEOUT_S = 30.0


class LLauncherMCP:
    """Minimal MCP client over stdio: one JSON-RPC request per line."""

    def __init__(self, stdin: IO[str], stdout: IO[str]):
        self._stdin = stdin
        self._stdout = stdout
        self._next_id = 0

    def _request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        req: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            req["params"] = params
        self._stdin.write(json.dumps(req) + "\n")
        self._stdin.flush()
        line = self._stdout.readline()
        if not line:
            raise RuntimeError(f"llauncher MCP closed stream during {method}")
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(f"llauncher MCP error: {resp['error']}")
        return resp.get("result")

    def initialize(self) -> dict[str, Any]:
        return self._request("initialize", {"protocolVersion": "2024-11-05"})

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """tools/call and unwrap the TextContent JSON payload."""
        result = self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        for item in (result or {}).get("content", []):
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except (ValueError, TypeError):
                    return item["text"]
        return result

    def close(self) -> None:
        try:
            self._stdin.close()
        except OSError:
            pass


class ModelManager:
    """Collects live NodeHealth for one node: VRAM probe + resident models."""

    def __init__(self, node: str, base_url: str):
        self.node = node
        self.base_url = base_url.rstrip("/")

    def collect_health(self, queue_depth: int = 0) -> NodeHealth:
        gpu = GpuHealth()
        try:
            used, total = probe_vram()
            gpu.vram_total_mb = total / 2**20
            gpu.vram_free_mb = (total - used) / 2**20
        except Exception:  # noqa: BLE001 - health must never crash the node
            pass

        models: dict[str, ModelEntry] = {}
        try:
            for entry in fetch_loaded_models(self.base_url):
                models[entry["model"]] = ModelEntry(
                    loaded=True, vram_mb=float(entry.get("vram_mb", 0.0))
                )
        except Exception:  # noqa: BLE001
            pass

        return NodeHealth(node=self.node, gpu=gpu, models=models, queue_depth=queue_depth)


def main() -> int:  # pragma: no cover - wired up in T3 (NATS health loop)
    """Placeholder entry: the publish loop arrives in T3."""
    print("model_manager: publish loop arrives in T3", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
