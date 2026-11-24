"""Docker adapter: containers as backend instances.

Compose is how a lot of this stack actually runs (``ollama_power``, and on
other hosts vLLM/TGI/llama.cpp images), so the control plane needs a path that
does not depend on llauncher being installed. This adapter talks to the docker
CLI directly and translates container state onto the memnc vocabulary.

Which containers count as backends is a policy, not "everything docker runs":
a container qualifies when it

* carries the ``memnc.backend`` label (opt-in, preferred), or
* is named in ``docker_backends`` (settings, comma-separated), or
* runs a known inference-server image (``ollama/ollama``, ``vllm/…``,
  ``ghcr.io/ggml-org/llama.cpp``, ``lmsysorg/sglang``, TGI, Triton).

Mesh infra (nats, workers) and unrelated services (searxng, open-webui) stay
out of the list unless explicitly labelled.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from .base import (
    FAILED,
    HEALTH_BAD,
    HEALTH_DOWN,
    HEALTH_OK,
    HEALTH_OPEN,
    RUNNING,
    STOPPED,
    UNKNOWN,
    ActionResult,
    BackendControl,
    BackendError,
    Service,
)

DEFAULT_LABEL = "memnc.backend"

# Prefix match on the image name; these images are model servers by definition.
INFERENCE_IMAGES = (
    "ollama/ollama",
    "docker.io/ollama/ollama",
    "vllm/vllm-openai",
    "docker.io/vllm/vllm-openai",
    "ghcr.io/ggml-org/llama.cpp",
    "lmsysorg/sglang",
    "ghcr.io/huggingface/text-generation-inference",
    "nvcr.io/nvidia/tritonserver",
)

STATE_MAP = {
    "running": RUNNING,
    "restarting": RUNNING,
    "exited": STOPPED,
    "created": STOPPED,
    "paused": STOPPED,
    "dead": FAILED,
    "removing": UNKNOWN,
}


class DockerControl:
    name = "docker"

    def __init__(
        self,
        binary: str = "docker",
        label: str = DEFAULT_LABEL,
        containers: str = "",
        timeout: float = 20.0,
    ) -> None:
        self.binary = binary
        self.label = label
        self.containers = [c.strip() for c in containers.split(",") if c.strip()]
        self.timeout = timeout

    # ── plumbing ───────────────────────────────────────────────────────────

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _run(self, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
        if not self.available():
            raise BackendError(f"{self.binary!r} not on PATH")
        try:
            return subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendError(f"{self.binary} {' '.join(args)} timed out") from exc

    # ── discovery policy ───────────────────────────────────────────────────

    def accepts(self, snap: dict) -> bool:
        """Should this container show up as a backend?"""
        name = str(snap.get("Names") or snap.get("Name") or "")
        if name in self.containers:
            return True
        labels = snap.get("Labels") or ""
        if f"{self.label}=" in labels or labels.strip() == self.label:
            return True
        image = str(snap.get("Image") or "").split("@", 1)[0]
        return any(image.startswith(prefix) for prefix in INFERENCE_IMAGES)

    # ── reading ────────────────────────────────────────────────────────────

    def services(self) -> list[Service]:
        if not self.available():
            return []
        try:
            proc = self._run("ps", "-a", "--no-trunc", "--format", "{{json .}}")
        except BackendError:
            return []
        if proc.returncode != 0:
            return []
        rows: list[Service] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(snap, dict) and self.accepts(snap):
                rows.append(self._to_service(snap))
        return rows

    def _to_service(self, snap: dict) -> Service:
        name = str(snap.get("Names") or snap.get("Name") or "?")
        native = str(snap.get("State") or "").lower()
        status = str(snap.get("Status") or "")
        state = STATE_MAP.get(native, UNKNOWN)

        health = self._health(native, status)
        labels = _parse_labels(snap.get("Labels"))
        project = labels.get("com.docker.compose.project", "")
        compose_service = labels.get("com.docker.compose.service", "")
        models = [
            m.strip()
            for m in (labels.get("memnc.models") or labels.get("memnc.model") or "").split(",")
            if m.strip()
        ]
        return Service(
            backend=self.name,
            name=name,
            state=state,
            health=health,
            endpoint=_endpoint(snap.get("Ports")),
            models=models,
            service=f"{project}/{compose_service}" if compose_service else name,
            detail=str(snap.get("Image") or ""),
        )

    @staticmethod
    def _health(native: str, status: str) -> str:
        if native != "running":
            return HEALTH_DOWN
        lowered = status.lower()
        if "(healthy)" in lowered:
            return HEALTH_OK
        if "(unhealthy)" in lowered:
            return HEALTH_BAD
        # Running, no healthcheck verdict: the port answers or it does not, but
        # docker does not tell us — report "open" rather than inventing a state.
        return HEALTH_OPEN

    # ── control ────────────────────────────────────────────────────────────

    def _verb(self, verb: str, name: str) -> ActionResult:
        try:
            proc = self._run(verb, name, timeout=180)
        except BackendError as exc:
            return ActionResult(False, verb, self.name, name, str(exc))
        out = (proc.stderr or proc.stdout).strip()
        if proc.returncode == 0 and verb == "start" and "already" in out.lower():
            out = f"already_running {name}"
        return ActionResult(
            ok=proc.returncode == 0,
            action=verb,
            backend=self.name,
            name=name,
            message=out.splitlines()[-1] if out else "",
        )

    def start(self, name: str) -> ActionResult:
        return self._verb("start", name)

    def stop(self, name: str) -> ActionResult:
        return self._verb("stop", name)

    def restart(self, name: str) -> ActionResult:
        return self._verb("restart", name)

    def logs(self, name: str, lines: int = 100) -> str:
        try:
            proc = self._run("logs", "--tail", str(lines), name, timeout=60)
        except BackendError as exc:
            raise BackendError(str(exc)) from exc
        if proc.returncode != 0 and not (proc.stdout or proc.stderr).strip():
            raise BackendError((proc.stderr or "no logs").strip())
        # docker splits container stdout/stderr; a log view wants both.
        return proc.stdout + proc.stderr


def _parse_labels(raw: object) -> dict[str, str]:
    """``docker ps --format {{json .}}`` gives labels as a comma-joined string."""
    labels: dict[str, str] = {}
    if isinstance(raw, dict):  # some docker versions emit an object
        return {str(k): str(v) for k, v in raw.items()}
    for chunk in str(raw or "").split(","):
        key, _, value = chunk.partition("=")
        key = key.strip()
        if key:
            labels[key] = value.strip()
    return labels


def _endpoint(ports: object) -> str:
    """First published host port of a container, normalised to 127.0.0.1:port."""
    for chunk in str(ports or "").split(","):
        chunk = chunk.strip()
        if "->" not in chunk:
            continue
        host_side = chunk.split("->", 1)[0].strip()
        if ":" not in host_side:
            continue
        host, _, port = host_side.rpartition(":")
        port = port.strip()
        if not port.isdigit():
            continue
        host = host.strip("[]")
        if host in ("", "0.0.0.0", "::", "[::]"):
            host = "127.0.0.1"
        return f"{host}:{port}"
    return ""


assert isinstance(DockerControl(), BackendControl)
