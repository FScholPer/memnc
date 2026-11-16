"""T4: model intents (load/unload/drain/ensure) map onto llauncher MCP tools.

Fake-MCP records every tool call; drain must stop every running model.
"""

from __future__ import annotations

import pytest

from memnc.model_manager import ModelManager
from memnc.schema import ModelIntentOp


class _FakeMCP:
    def __init__(self, models):
        self.calls: list[tuple[str, dict]] = []
        self._models = models

    def call(self, name, arguments=None):
        self.calls.append((name, arguments or {}))
        if name == "list_models":
            return self._models
        if name == "validate_models":
            return {"ok": True}
        return {"ok": True}

    def close(self):
        pass


def _registry():
    """llauncher list_models shape: one running, one stopped."""
    return {
        "models": [
            {
                "identification": {"name": "coder-7b"},
                "status": {"state": "running", "port": 8097},
            },
            {
                "identification": {"name": "critic-3b"},
                "status": {"state": "stopped"},
            },
        ]
    }


@pytest.fixture
def manager():
    return ModelManager(node="node-a", base_url="http://x")


async def test_load_calls_start_server(manager):
    mcp = _FakeMCP(_registry())
    result = manager.handle_intent(
        ModelIntentOp.LOAD, model="critic-3b", mcp=mcp, port=8098
    )
    assert result["ok"] is True
    assert ("start_server", {"model_name": "critic-3b", "port": 8098}) in mcp.calls


async def test_load_unknown_model_is_rejected(manager):
    mcp = _FakeMCP(_registry())
    result = manager.handle_intent(ModelIntentOp.LOAD, model="nope", mcp=mcp)
    assert result["ok"] is False
    assert result["reason"] == "unknown_model"
    assert not any(name == "start_server" for name, _ in mcp.calls)


async def test_unload_stops_running_port(manager):
    mcp = _FakeMCP(_registry())
    result = manager.handle_intent(ModelIntentOp.UNLOAD, model="coder-7b", mcp=mcp)
    assert result["ok"] is True
    assert ("stop_server", {"port": 8097}) in mcp.calls


async def test_unload_not_running_is_idempotent(manager):
    mcp = _FakeMCP(_registry())
    result = manager.handle_intent(ModelIntentOp.UNLOAD, model="critic-3b", mcp=mcp)
    assert result["ok"] is True
    assert result["reason"] == "not_running"


async def test_drain_stops_every_running_model(manager):
    mcp = _FakeMCP(_registry())
    result = manager.handle_intent(ModelIntentOp.DRAIN, mcp=mcp)
    assert result["ok"] is True
    stopped = [a.get("port") for n, a in mcp.calls if n == "stop_server"]
    assert stopped == [8097]


async def test_ensure_validates_then_starts(manager):
    mcp = _FakeMCP(_registry())
    result = manager.handle_intent(
        ModelIntentOp.ENSURE, model="critic-3b", mcp=mcp, port=8098
    )
    assert result["ok"] is True
    names = [n for n, _ in mcp.calls]
    assert names.index("validate_models") < names.index("start_server")


async def test_missing_model_for_load_is_rejected(manager):
    mcp = _FakeMCP(_registry())
    result = manager.handle_intent(ModelIntentOp.LOAD, mcp=mcp)
    assert result["ok"] is False
    assert result["reason"] == "bad_request"
