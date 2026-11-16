"""Failure classification (15.4.3, error-aware retry).

One-shot retries restart the full pipeline on the same broken route. The
first slice of the durable-tasks design classifies WHY a step failed so the
orchestrator can rotate the role's model instead of re-picking the one that
cannot do the job:

- ``timeout`` — the worker or inference call hit a deadline (a too-slow
  model is also a model-level failure: rotating gives a faster one a try).
- ``server`` — HTTP 5xx / connection errors from the inference backend.
- ``invalid_output`` — the model answered but the output did not validate
  (e.g. the small-model JSON loop).
- ``unknown`` — everything else (classification stays honest; rotation
  never fires on unknown failures).
"""

from __future__ import annotations

TIMEOUT = "timeout"
SERVER = "server"
INVALID_OUTPUT = "invalid_output"
UNKNOWN = "unknown"

# Model-level failures: worth trying a different model for the role.
MODEL_LEVEL_KINDS = frozenset({TIMEOUT, SERVER, INVALID_OUTPUT})

_TIMEOUT_MARKERS = (
    "timed out",
    "timeout",
    "timeouterror",
    "readtimeout",
)
_SERVER_MARKERS = (
    "500",
    "502",
    "503",
    "internal server error",
    "connection refused",
    "connectionerror",
    "connectcallfailed",
    "service unavailable",
)
_INVALID_MARKERS = (
    "no json object found",
    "invalid output",
    "validationerror",
    "validation error",
    "parse error",
    "unparsable",
    "not valid json",
)


def classify_failure(error_text: str | None) -> str:
    """Classify a step-failure error string. Timeout wins over the others:
    a ReadTimeout inside a 500-shaped message is still a deadline failure."""
    text = (error_text or "").lower()
    if not text:
        return UNKNOWN
    if any(m in text for m in _TIMEOUT_MARKERS):
        return TIMEOUT
    if any(m in text for m in _INVALID_MARKERS):
        return INVALID_OUTPUT
    if any(m in text for m in _SERVER_MARKERS):
        return SERVER
    return UNKNOWN
