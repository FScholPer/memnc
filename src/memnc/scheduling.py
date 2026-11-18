"""15.4.6: VRAM as a schedulable resource — the fit gate.

A model that fits no card is rejected BEFORE load (the Kubernetes
FailedScheduling analogy): no half-started substitutes, no ngl rest-split
fallback. A load that ends up mostly in system RAM despite the gate is
detected after load and discarded.

Pure logic only; the worker wires this into the load/pull path.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

# Absorbs KV cache, compute buffers and graph overhead on top of the weight
# bytes. Callers that know a model's real KV footprint can pass it explicitly
# via kv_cache_bytes; the headroom still applies on top.
HEADROOM = 1.15

# A resident footprint below this fraction of the model's weight bytes means
# the loader spilled the rest to system RAM (rest-split).
REST_SPLIT_THRESHOLD = 0.9


@dataclass
class FitResult:
    """Outcome of an admission check; reason is a stable log token."""

    fits: bool
    reason: str


def fit(
    min_free_bytes: int | float,
    model_bytes: int | float | None,
    kv_cache_bytes: int | float = 0,
    placement: str = "full",
    hot_cache_capable: bool = False,
    strict: bool = True,
) -> FitResult:
    """Admission check before a load: does the model fit the node at all?

    min_free_bytes is the WORST card's free VRAM — placement gates on what a
    single card can hold, never on a sum across cards. An unknown size in
    strict mode rejects (fail closed); in lenient mode it passes with a
    reason so a broken size source cannot brick every load.
    """
    if placement == "moe-hot-cache" and not hot_cache_capable:
        return FitResult(False, "unschedulable:no-hot-cache")
    if not model_bytes:
        if strict:
            return FitResult(False, "unschedulable:unknown-size")
        return FitResult(True, "unknown-size")
    need = (float(model_bytes) + float(kv_cache_bytes)) * HEADROOM
    if need > float(min_free_bytes):
        return FitResult(False, "unschedulable:fits-no-card")
    return FitResult(True, "ok")


def rest_split(
    size_vram_bytes: int | float, model_bytes: int | float | None
) -> bool:
    """True when the resident VRAM footprint is far below the weight bytes.

    Only meaningful with a known positive model size; a missing size never
    flags (no data is not a verdict).
    """
    if not model_bytes or not size_vram_bytes:
        return False
    return float(size_vram_bytes) < REST_SPLIT_THRESHOLD * float(model_bytes)


def placement_for(model: str, moe_hot_cache_models: list[str]) -> str:
    """Registry-style placement lookup for models without a real registry.

    v1 carries the MoE-with-hot-cache special case as a settings list of
    fnmatch patterns; everything else is a plain full-placement model.
    """
    for pattern in moe_hot_cache_models:
        if fnmatch.fnmatch(model, pattern):
            return "moe-hot-cache"
    return "full"
