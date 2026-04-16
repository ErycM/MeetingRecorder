"""
Tests for src/app/npu_guard.py — NPU model filter and enforcement.

Uses unittest.mock to patch requests.get so these tests run without a
real Lemonade server.

Covers DEFINE criteria:
- "NPU model filter"
- "No silent CPU fallback"
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import app.npu_guard as npu_module
from app.npu_guard import (
    NPU_ALLOWLIST,
    ensure_ready,
    list_npu_models,
)

_SERVER_URL = "http://localhost:13305"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(json_data: object, status: int = 200) -> MagicMock:
    """Build a mock requests.Response."""
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = json_data
    mock.raise_for_status = MagicMock()
    if status >= 400:
        mock.raise_for_status.side_effect = requests.HTTPError(response=mock)
    return mock


# ---------------------------------------------------------------------------
# list_npu_models
# ---------------------------------------------------------------------------


class TestListNpuModels:
    def test_npu_models_with_backend_field_returned(self) -> None:
        """Models with backend containing 'npu' are returned."""
        response_data = [
            {"id": "Whisper-Large-v3-Turbo", "backend": "whispercpp:npu"},
            {"id": "some-cpu-model", "backend": "cpu"},
        ]
        with patch("requests.get", return_value=_mock_response(response_data)):
            models = list_npu_models(_SERVER_URL)

        assert "Whisper-Large-v3-Turbo" in models
        assert "some-cpu-model" not in models

    def test_npu_models_with_execution_provider_field(self) -> None:
        """Models with execution_provider containing 'npu' are returned."""
        response_data = [
            {
                "id": "whisper-medium.en",
                "execution_provider": "NPUExecutionProvider",
            },
            {
                "id": "whisper-cpu",
                "execution_provider": "CPUExecutionProvider",
            },
        ]
        with patch("requests.get", return_value=_mock_response(response_data)):
            models = list_npu_models(_SERVER_URL)

        assert "whisper-medium.en" in models
        assert "whisper-cpu" not in models

    def test_npu_models_with_provider_field(self) -> None:
        """Models with provider containing 'npu' are returned."""
        response_data = [
            {"id": "whisper-large-v3", "provider": "npu_provider"},
            {"id": "whisper-cpu", "provider": "cpu_provider"},
        ]
        with patch("requests.get", return_value=_mock_response(response_data)):
            models = list_npu_models(_SERVER_URL)

        assert "whisper-large-v3" in models
        assert "whisper-cpu" not in models

    def test_no_provider_field_falls_back_to_allowlist(self) -> None:
        """When no model has a provider field, allowlist is used."""
        response_data = [
            {"id": "whisper-medium.en"},  # in allowlist
            {"id": "some-unknown-model"},  # NOT in allowlist
        ]
        with patch("requests.get", return_value=_mock_response(response_data)):
            models = list_npu_models(_SERVER_URL)

        assert "whisper-medium.en" in models
        assert "some-unknown-model" not in models

    def test_no_provider_field_all_unknown_returns_empty(self) -> None:
        """Models not in allowlist and no provider field → empty result."""
        response_data = [
            {"id": "gpt-4"},
            {"id": "llama-cpu"},
        ]
        with patch("requests.get", return_value=_mock_response(response_data)):
            models = list_npu_models(_SERVER_URL)

        assert models == []

    def test_empty_model_list_returns_empty(self) -> None:
        """Empty model list from server → empty result."""
        with patch("requests.get", return_value=_mock_response([])):
            models = list_npu_models(_SERVER_URL)

        assert models == []

    def test_wrapped_data_key(self) -> None:
        """Response wrapped in {'data': [...]} is handled correctly."""
        response_data = {
            "data": [
                {"id": "Whisper-Large-v3-Turbo", "backend": "whispercpp:npu"},
            ]
        }
        with patch("requests.get", return_value=_mock_response(response_data)):
            models = list_npu_models(_SERVER_URL)

        assert "Whisper-Large-v3-Turbo" in models

    def test_http_error_raises(self) -> None:
        """HTTP 500 raises requests.HTTPError."""
        with patch("requests.get", return_value=_mock_response({}, status=500)):
            with pytest.raises(requests.HTTPError):
                list_npu_models(_SERVER_URL)

    def test_connection_error_propagates(self) -> None:
        """ConnectionError from requests propagates (not swallowed)."""
        with patch("requests.get", side_effect=requests.ConnectionError("refused")):
            with pytest.raises(requests.ConnectionError):
                list_npu_models(_SERVER_URL)

    def test_npu_keyword_case_insensitive(self) -> None:
        """NPU keyword match is case-insensitive."""
        response_data = [
            {"id": "model-a", "backend": "NPU_BACKEND"},
            {"id": "model-b", "backend": "NpuFoo"},
        ]
        with patch("requests.get", return_value=_mock_response(response_data)):
            models = list_npu_models(_SERVER_URL)

        assert "model-a" in models
        assert "model-b" in models


# ---------------------------------------------------------------------------
# ensure_ready — ENFORCE_NPU=True (default)
# ---------------------------------------------------------------------------


class TestEnsureReadyEnforced:
    def test_npu_model_available_returns_ready(self) -> None:
        """NPU model available → NPUStatus(ready=True)."""
        data = [{"id": "Whisper-Large-v3-Turbo", "backend": "whispercpp:npu"}]
        with patch("requests.get", return_value=_mock_response(data)):
            status = ensure_ready(_SERVER_URL)

        assert status.ready is True
        assert "Whisper-Large-v3-Turbo" in status.available_models
        assert status.error is None

    def test_no_npu_models_returns_not_ready(self) -> None:
        """No NPU models → NPUStatus(ready=False) with diagnostic message."""
        data = [{"id": "cpu-model", "backend": "cpu"}]
        with patch("requests.get", return_value=_mock_response(data)):
            status = ensure_ready(_SERVER_URL)

        assert status.ready is False
        assert status.available_models == []
        assert status.error is not None
        assert "NPU" in status.error or "npu" in status.error.lower()

    def test_server_unreachable_returns_not_ready(self) -> None:
        """Server unreachable → NPUStatus(ready=False) with error, no raise."""
        with patch(
            "requests.get",
            side_effect=requests.ConnectionError("Connection refused"),
        ):
            status = ensure_ready(_SERVER_URL)

        assert status.ready is False
        assert status.error is not None
        assert (
            "unreachable" in status.error.lower()
            or "connection" in status.error.lower()
        )

    def test_http_error_returns_not_ready(self) -> None:
        """HTTP error → NPUStatus(ready=False) with error, no raise."""
        with patch("requests.get", return_value=_mock_response({}, status=503)):
            status = ensure_ready(_SERVER_URL)

        assert status.ready is False
        assert status.error is not None

    def test_fallback_allowlist_model_returns_ready(self) -> None:
        """Model in allowlist (no provider field) → NPUStatus(ready=True)."""
        data = [{"id": "whisper-medium.en"}]
        with patch("requests.get", return_value=_mock_response(data)):
            status = ensure_ready(_SERVER_URL)

        assert status.ready is True
        assert "whisper-medium.en" in status.available_models

    def test_error_message_contains_settings_hint(self) -> None:
        """The NPU-not-available error message hints at Settings → Diagnostics."""
        data = []
        with patch("requests.get", return_value=_mock_response(data)):
            status = ensure_ready(_SERVER_URL)

        assert status.error is not None
        assert "Diagnostics" in status.error or "diagnostics" in status.error.lower()


# ---------------------------------------------------------------------------
# ensure_ready — ENFORCE_NPU=False (monkeypatched)
# ---------------------------------------------------------------------------


class TestEnsureReadyNotEnforced:
    def test_enforce_false_cpu_model_accepted(self, monkeypatch) -> None:
        """With ENFORCE_NPU=False, any loaded model counts as available."""
        monkeypatch.setattr(npu_module, "ENFORCE_NPU", False)

        data = [{"id": "cpu-only-model"}]
        with patch("requests.get", return_value=_mock_response(data)):
            status = ensure_ready(_SERVER_URL)

        assert status.ready is True
        assert "cpu-only-model" in status.available_models

    def test_enforce_false_empty_list_not_ready(self, monkeypatch) -> None:
        """With ENFORCE_NPU=False, empty model list → not ready."""
        monkeypatch.setattr(npu_module, "ENFORCE_NPU", False)

        with patch("requests.get", return_value=_mock_response([])):
            status = ensure_ready(_SERVER_URL)

        assert status.ready is False

    def test_enforce_false_server_unreachable_not_ready(self, monkeypatch) -> None:
        """With ENFORCE_NPU=False, unreachable server → not ready."""
        monkeypatch.setattr(npu_module, "ENFORCE_NPU", False)

        with patch(
            "requests.get",
            side_effect=requests.ConnectionError("refused"),
        ):
            status = ensure_ready(_SERVER_URL)

        assert status.ready is False


# ---------------------------------------------------------------------------
# Allowlist content
# ---------------------------------------------------------------------------


class TestAllowlist:
    def test_allowlist_contains_expected_models(self) -> None:
        """The hardcoded allowlist contains the production NPU model IDs."""
        assert "Whisper-Large-v3-Turbo" in NPU_ALLOWLIST
        assert "whisper-medium.en" in NPU_ALLOWLIST
        assert "whisper-large-v3" in NPU_ALLOWLIST

    def test_allowlist_is_frozenset(self) -> None:
        assert isinstance(NPU_ALLOWLIST, frozenset)
