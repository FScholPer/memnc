"""T2: model_manager — llauncher MCP client + per-node health collection.

The model_manager is the thin per-node process that owns serving control:
it talks MCP (stdio, newline-delimited JSON-RPC) to llauncher and reports
live NodeHealth. The MCP client is minimal on purpose — initialize +
tools/call is all memnc needs (start/stop/swap, list_models,
validate_models, backend_status). Health collection reuses the T2
vram_probe numbers and Ollama /api/ps truth; it never crashes the node.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import IO, Any

from memnc.schema import (
    SUBJECT_HEALTH,
    Envelope,
    GpuHealth,
    Kind,
    ModelEntry,
    ModelIntentOp,
    NodeHealth,
)
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

    # -- T4: intents (load/unload/drain/ensure) → llauncher tools -----------

    def handle_intent(
        self,
        op: ModelIntentOp,
        model: str | None = None,
        mcp: Any | None = None,
        port: int | None = None,
        default_port: int = 8080,
    ) -> dict[str, Any]:
        """Run one intent against llauncher MCP. Returns a result dict.

        Errors are returned as {"ok": False, "reason": ...} — an intent
        must never crash the model_manager process.
        """
        log = logging.getLogger(__name__)
        result: dict[str, Any]
        if mcp is None:
            result = {"ok": False, "op": op.value, "model": model, "reason": "no_mcp"}
        else:
            try:
                result = self._run_intent(op, model, mcp, port, default_port)
            except Exception as exc:  # noqa: BLE001 - intents must never crash
                result = {
                    "ok": False,
                    "op": op.value,
                    "model": model,
                    "reason": f"mcp_error: {exc}",
                }
        log.info(
            "intent %s: model=%s ok=%s port=%s%s",
            op.value,
            model,
            result.get("ok"),
            result.get("started_port") or result.get("stopped_port")
            or result.get("stopped_ports"),
            "" if result.get("ok") else f' reason={result.get("reason")}',
        )
        return result

    def _run_intent(
        self,
        op: ModelIntentOp,
        model: str | None,
        mcp: Any,
        port: int | None,
        default_port: int,
    ) -> dict[str, Any]:
        base: dict[str, Any] = {"ok": True, "op": op.value, "model": model}

        if op is ModelIntentOp.DRAIN:
            stopped: list[int] = []
            for entry in self._running_models(mcp):
                stopped.append(entry["status"]["port"])
                mcp.call("stop_server", {"port": entry["status"]["port"]})
            return {**base, "stopped_ports": stopped}

        if not model:
            return {**base, "ok": False, "reason": "bad_request"}

        known = self._known_models(mcp)
        if model not in known:
            return {**base, "ok": False, "reason": "unknown_model"}

        if op is ModelIntentOp.UNLOAD:
            running = known[model]
            if running is None:
                return {**base, "reason": "not_running"}
            mcp.call("stop_server", {"port": running["port"]})
            return {**base, "stopped_port": running["port"]}

        target_port = port or default_port
        if op is ModelIntentOp.ENSURE:
            mcp.call("validate_models", {"model_name": model})
        mcp.call("start_server", {"model_name": model, "port": target_port})
        return {**base, "started_port": target_port}

    def _models_registry(self, mcp: Any) -> list[dict[str, Any]]:
        doc = mcp.call("list_models") or {}
        return doc.get("models", [])

    def _running_models(self, mcp: Any) -> list[dict[str, Any]]:
        return [
            e for e in self._models_registry(mcp)
            if e.get("status", {}).get("state") == "running"
        ]

    def _known_models(self, mcp: Any) -> dict[str, dict[str, Any] | None]:
        """name → running status (with port) or None when stopped."""
        known: dict[str, dict[str, Any] | None] = {}
        for entry in self._models_registry(mcp):
            status = entry.get("status", {})
            name = entry.get("identification", {}).get("name")
            if name:
                known[name] = status if status.get("state") == "running" else None
        return known


class HealthPublisher:
    """T3: publishes NodeHealth on mesh.node.health.<node> every interval.

    Health must never crash the node: collect errors produce zeroed
    numbers (still published) and the loop only exits on the stop event.
    """

    def __init__(self, manager: ModelManager, bus, interval_s: int = 5):
        self.manager = manager
        self.bus = bus
        self.interval_s = interval_s

    def _envelope(self, health: NodeHealth) -> Envelope:
        task_id = f"health-{self.manager.node}"
        return Envelope(
            task_id=task_id,
            trace_id=task_id,
            kind=Kind.HEALTH,
            node=self.manager.node,
            payload=health.model_dump(),
        )

    async def run(self, stop: asyncio.Event) -> None:
        log = logging.getLogger(__name__)
        while not stop.is_set():
            health = await asyncio.to_thread(self.manager.collect_health)
            try:
                await self.bus.publish_envelope(
                    SUBJECT_HEALTH.format(node=self.manager.node),
                    self._envelope(health),
                )
            except Exception:  # noqa: BLE001 - publishing must not kill the loop
                pass
            else:
                gpu = health.gpu
                log.info(
                    "health published: node=%s vram_free_mb=%.0f/%.0f "
                    "models=%d queue=%d",
                    health.node,
                    gpu.vram_free_mb,
                    gpu.vram_total_mb,
                    len(health.models),
                    health.queue_depth,
                )
            if stop.is_set():
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_s)
            except TimeoutError:
                pass


def main() -> int:  # pragma: no cover - real process entry (T3)
    """model_manager process: NATS health loop until SIGINT/SIGTERM."""
    import asyncio

    from memnc.bus import Bus
    from memnc.config import Settings

    async def _run() -> None:
        logging.basicConfig(
            level=logging.INFO,
            format='{"ts": "%(asctime)s", "level": "INFO", "msg": "%(message)s"}',
        )
        settings = Settings()
        bus = Bus(settings)
        await bus.connect()
        manager = ModelManager(
            node=settings.node_name, base_url=settings.inference_base_url
        )
        publisher = HealthPublisher(
            manager=manager, bus=bus, interval_s=settings.health_interval_s
        )
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await publisher.run(stop)
        await bus.close()

    asyncio.run(_run())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
