"""
NPU guard — verify Lemonade NPU model availability (ADR-6).

ENFORCE_NPU is a module-level constant (not in config.toml, not an env var).
Flip it to False only for downstream forks targeting non-Ryzen-AI hardware.

The Lemonade /api/v1/models response varies across versions. Known shapes:

  [{"id": "Whisper-Large-v3-Turbo", "backend": "whispercpp:npu", ...}, ...]
  [{"id": "whisper-medium.en", "execution_provider": "NPUExecutionProvider"}, ...]
  [{"id": "whisper-medium.en"}]   <-- field absent; fall back to allowlist

We check the following fields (first match wins):
  - "backend"            — contains "npu" (case-insensitive)
  - "execution_provider" — contains "npu" (case-insensitive)
  - "provider"           — contains "npu" (case-insensitive)

If none of those fields is present on ANY model entry, we fall back to the
hardcoded NPU allowlist.  A model not in the allowlist is not surfaced.

Thread-safety note (I-3): ensure_ready() and list_npu_models() make HTTP
requests and MUST NOT be called from T1 (the Tk mainloop). Call them from
a worker thread and dispatch the result via window.after(0, ...).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants (ADR-6)
# ---------------------------------------------------------------------------

ENFORCE_NPU: bool = True

# Hardcoded allowlist used when Lemonade's /api/v1/models response has no
# provider/backend field to filter on.
NPU_ALLOWLIST: frozenset[str] = frozenset(
    {
        "Whisper-Large-v3-Turbo",
        "whisper-medium.en",
        "whisper-large-v3",
    }
)

# Fields we inspect (in priority order) to detect NPU execution provider.
_NPU_PROVIDER_FIELDS = ("backend", "execution_provider", "provider")
_NPU_KEYWORD = "npu"

# Default Lemonade REST base URL
DEFAULT_SERVER_URL = "http://localhost:13305"

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class NPUStatus:
    """Result of an NPU readiness check."""

    ready: bool
    available_models: list[str] = field(default_factory=list)
    error: str | None = None


class NPUNotAvailable(Exception):
    """Raised by ensure_ready() when ENFORCE_NPU is True and no NPU model is found."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_npu_models(server_url: str = DEFAULT_SERVER_URL) -> list[str]:
    """Query Lemonade /api/v1/models and return NPU-capable model IDs.

    Strategy:
    1. HTTP-GET {server_url}/api/v1/models.
    2. If any entry has a backend/execution_provider/provider field, filter
       to those containing "npu" (case-insensitive).
    3. If NO entry has any of those fields, fall back to NPU_ALLOWLIST:
       return only model IDs that are in the allowlist.

    Returns an empty list if no NPU-capable models are found or if the
    server is unreachable (in which case it raises requests.RequestException).

    Raises
    ------
    requests.RequestException
        On network/HTTP error — let the caller decide how to handle.
    """
    url = f"{server_url.rstrip('/')}/api/v1/models"
    log.debug("[LEMONADE] GET %s", url)

    resp = requests.get(url, timeout=10)
    resp.raise_for_status()

    models: list[dict] = resp.json()
    if not isinstance(models, list):
        # Some versions wrap in {"data": [...]}
        if isinstance(models, dict) and "data" in models:
            models = models["data"]
        else:
            log.warning("[LEMONADE] Unexpected /api/v1/models response shape")
            models = []

    log.debug("[LEMONADE] %d model entries returned", len(models))

    # Check if any entry has a provider-type field
    has_provider_field = any(
        any(f in entry for f in _NPU_PROVIDER_FIELDS) for entry in models
    )

    if has_provider_field:
        return _filter_by_provider(models)
    else:
        return _filter_by_allowlist(models)


def ensure_ready(server_url: str = DEFAULT_SERVER_URL) -> NPUStatus:
    """Check NPU readiness and return an NPUStatus.

    If ENFORCE_NPU is True and no NPU model is available, returns
    NPUStatus(ready=False, error=<diagnostic message>).

    If ENFORCE_NPU is False, any model that the server lists counts as
    available (for downstream non-NPU builds).

    This function does NOT raise — errors are communicated via the returned
    NPUStatus so callers can dispatch UI updates without needing try/except
    at every call site.

    Callers that need to raise on failure (e.g. the orchestrator's startup
    check) should call _raise_if_not_ready() after this.
    """
    try:
        if ENFORCE_NPU:
            npu_models = list_npu_models(server_url)
            if npu_models:
                log.debug("[LEMONADE] NPU models available: %s", npu_models)
                return NPUStatus(ready=True, available_models=npu_models)
            else:
                msg = (
                    "No NPU-backed Whisper model available — see Settings → Diagnostics"
                )
                log.warning("[LEMONADE] %s", msg)
                return NPUStatus(ready=False, available_models=[], error=msg)
        else:
            # ENFORCE_NPU=False: accept any loaded model
            models = _list_all_models(server_url)
            log.debug("[LEMONADE] ENFORCE_NPU=False, accepting all models: %s", models)
            return NPUStatus(ready=bool(models), available_models=models)

    except requests.RequestException as exc:
        msg = f"Lemonade server unreachable: {exc}"
        log.warning("[LEMONADE] %s", msg)
        return NPUStatus(ready=False, available_models=[], error=msg)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _filter_by_provider(models: list[dict]) -> list[str]:
    """Return model IDs whose provider/backend field contains 'npu'."""
    result: list[str] = []
    for entry in models:
        for field_name in _NPU_PROVIDER_FIELDS:
            value = entry.get(field_name, "")
            if value and _NPU_KEYWORD in str(value).lower():
                model_id = entry.get("id") or entry.get("name") or entry.get("model")
                if model_id:
                    result.append(str(model_id))
                break
    return result


def _filter_by_allowlist(models: list[dict]) -> list[str]:
    """Return model IDs that appear in NPU_ALLOWLIST."""
    result: list[str] = []
    for entry in models:
        model_id = entry.get("id") or entry.get("name") or entry.get("model")
        if model_id and str(model_id) in NPU_ALLOWLIST:
            result.append(str(model_id))
    return result


def _list_all_models(server_url: str) -> list[str]:
    """Return all model IDs without NPU filtering (ENFORCE_NPU=False path)."""
    url = f"{server_url.rstrip('/')}/api/v1/models"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    models = resp.json()
    if isinstance(models, dict) and "data" in models:
        models = models["data"]
    if not isinstance(models, list):
        return []
    result: list[str] = []
    for entry in models:
        model_id = entry.get("id") or entry.get("name") or entry.get("model")
        if model_id:
            result.append(str(model_id))
    return result
