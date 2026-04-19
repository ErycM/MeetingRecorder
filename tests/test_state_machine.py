"""
Tests for src/app/state.py — StateMachine legal/illegal transitions.

Covers DEFINE success criterion: "State machine legality".
All tests run off T1 using enforce_thread=False to avoid needing a Tk loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.state import (
    AppState,
    ErrorReason,
    IllegalTransition,
    StateMachine,
    WrongThreadError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_sm(callback=None, *, enforce_thread: bool = False) -> StateMachine:
    """Construct a StateMachine with thread enforcement off for unit tests."""
    return StateMachine(on_change=callback, enforce_thread=enforce_thread)


# ---------------------------------------------------------------------------
# Legal transitions
# ---------------------------------------------------------------------------


class TestLegalTransitions:
    def test_idle_to_armed(self) -> None:
        sm = make_sm()
        sm.transition(AppState.ARMED)
        assert sm.current is AppState.ARMED

    def test_armed_to_recording(self) -> None:
        sm = make_sm()
        sm.transition(AppState.ARMED)
        sm.transition(AppState.RECORDING)
        assert sm.current is AppState.RECORDING

    def test_recording_to_transcribing(self) -> None:
        sm = make_sm()
        sm.transition(AppState.ARMED)
        sm.transition(AppState.RECORDING)
        sm.transition(AppState.TRANSCRIBING)
        assert sm.current is AppState.TRANSCRIBING

    def test_transcribing_to_saving(self) -> None:
        sm = make_sm()
        sm.transition(AppState.ARMED)
        sm.transition(AppState.RECORDING)
        sm.transition(AppState.TRANSCRIBING)
        sm.transition(AppState.SAVING)
        assert sm.current is AppState.SAVING

    def test_saving_to_idle(self) -> None:
        sm = make_sm()
        sm.transition(AppState.ARMED)
        sm.transition(AppState.RECORDING)
        sm.transition(AppState.SAVING)
        sm.transition(AppState.IDLE)
        assert sm.current is AppState.IDLE

    def test_recording_to_saving_streaming_path(self) -> None:
        """Streaming path skips TRANSCRIBING: RECORDING -> SAVING directly."""
        sm = make_sm()
        sm.transition(AppState.ARMED)
        sm.transition(AppState.RECORDING)
        sm.transition(AppState.SAVING)
        assert sm.current is AppState.SAVING

    def test_armed_to_idle(self) -> None:
        """ARMED -> IDLE is legal (cancelled before recording started)."""
        sm = make_sm()
        sm.transition(AppState.ARMED)
        sm.transition(AppState.IDLE)
        assert sm.current is AppState.IDLE

    def test_full_batch_path(self) -> None:
        """IDLE -> ARMED -> RECORDING -> TRANSCRIBING -> SAVING -> IDLE."""
        sm = make_sm()
        path = [
            AppState.ARMED,
            AppState.RECORDING,
            AppState.TRANSCRIBING,
            AppState.SAVING,
            AppState.IDLE,
        ]
        for state in path:
            sm.transition(state)
        assert sm.current is AppState.IDLE

    def test_error_recovery_via_reset(self) -> None:
        """ERROR -> IDLE is allowed only via reset()."""
        sm = make_sm()
        sm.transition(AppState.ERROR, reason=ErrorReason.LEMONADE_UNREACHABLE)
        assert sm.current is AppState.ERROR
        sm.reset()
        assert sm.current is AppState.IDLE


# ---------------------------------------------------------------------------
# ERROR reachable from any state
# ---------------------------------------------------------------------------


class TestErrorReachable:
    @pytest.mark.parametrize(
        "setup_states",
        [
            [],  # from IDLE
            [AppState.ARMED],  # from ARMED
            [AppState.ARMED, AppState.RECORDING],  # from RECORDING
            [AppState.ARMED, AppState.RECORDING, AppState.TRANSCRIBING],  # TRANSCRIBING
            [AppState.ARMED, AppState.RECORDING, AppState.SAVING],  # SAVING
        ],
    )
    def test_error_reachable_from_state(self, setup_states: list[AppState]) -> None:
        sm = make_sm()
        for state in setup_states:
            sm.transition(state)
        sm.transition(AppState.ERROR, reason=ErrorReason.WASAPI_DEVICE_LOST)
        assert sm.current is AppState.ERROR

    @pytest.mark.parametrize(
        "reason",
        list(ErrorReason),
    )
    def test_all_error_reasons_accepted(self, reason: ErrorReason) -> None:
        sm = make_sm()
        sm.transition(AppState.ERROR, reason=reason)
        assert sm.current is AppState.ERROR


# ---------------------------------------------------------------------------
# Illegal transitions
# ---------------------------------------------------------------------------


class TestIllegalTransitions:
    def test_idle_to_recording_illegal(self) -> None:
        sm = make_sm()
        with pytest.raises(IllegalTransition):
            sm.transition(AppState.RECORDING)

    def test_idle_to_saving_illegal(self) -> None:
        sm = make_sm()
        with pytest.raises(IllegalTransition):
            sm.transition(AppState.SAVING)

    def test_idle_to_transcribing_illegal(self) -> None:
        sm = make_sm()
        with pytest.raises(IllegalTransition):
            sm.transition(AppState.TRANSCRIBING)

    def test_armed_to_transcribing_illegal(self) -> None:
        sm = make_sm()
        sm.transition(AppState.ARMED)
        with pytest.raises(IllegalTransition):
            sm.transition(AppState.TRANSCRIBING)

    def test_armed_to_saving_illegal(self) -> None:
        sm = make_sm()
        sm.transition(AppState.ARMED)
        with pytest.raises(IllegalTransition):
            sm.transition(AppState.SAVING)

    def test_recording_to_idle_illegal(self) -> None:
        sm = make_sm()
        sm.transition(AppState.ARMED)
        sm.transition(AppState.RECORDING)
        with pytest.raises(IllegalTransition):
            sm.transition(AppState.IDLE)

    def test_saving_to_armed_illegal(self) -> None:
        sm = make_sm()
        sm.transition(AppState.ARMED)
        sm.transition(AppState.RECORDING)
        sm.transition(AppState.SAVING)
        with pytest.raises(IllegalTransition):
            sm.transition(AppState.ARMED)

    def test_error_to_armed_illegal(self) -> None:
        sm = make_sm()
        sm.transition(AppState.ERROR, reason=ErrorReason.MODEL_NOT_NPU)
        with pytest.raises(IllegalTransition):
            sm.transition(AppState.ARMED)

    def test_error_to_error_illegal(self) -> None:
        """ERROR -> ERROR self-transition is blocked."""
        sm = make_sm()
        sm.transition(AppState.ERROR, reason=ErrorReason.LEMONADE_UNREACHABLE)
        with pytest.raises(IllegalTransition):
            sm.transition(AppState.ERROR, reason=ErrorReason.MODEL_NOT_NPU)

    def test_reset_from_non_error_raises(self) -> None:
        """reset() on a non-ERROR state should raise IllegalTransition."""
        sm = make_sm()
        sm.transition(AppState.ARMED)
        with pytest.raises(IllegalTransition):
            sm.reset()


# ---------------------------------------------------------------------------
# Callback behaviour
# ---------------------------------------------------------------------------


class TestCallbacks:
    def test_callback_fires_once_per_transition(self) -> None:
        calls: list[tuple] = []
        sm = make_sm(callback=lambda old, new, reason: calls.append((old, new, reason)))

        sm.transition(AppState.ARMED)
        assert len(calls) == 1
        assert calls[0] == (AppState.IDLE, AppState.ARMED, None)

    def test_callback_receives_error_reason(self) -> None:
        calls: list[tuple] = []
        sm = make_sm(callback=lambda old, new, reason: calls.append((old, new, reason)))

        sm.transition(AppState.ERROR, reason=ErrorReason.LEMONADE_UNREACHABLE)
        assert calls[0][2] is ErrorReason.LEMONADE_UNREACHABLE

    def test_callback_fires_on_reset(self) -> None:
        calls: list[tuple] = []
        sm = make_sm(callback=lambda old, new, reason: calls.append((old, new, reason)))
        sm.transition(AppState.ERROR, reason=ErrorReason.WASAPI_DEVICE_LOST)
        sm.reset()

        assert len(calls) == 2
        assert calls[1] == (AppState.ERROR, AppState.IDLE, None)

    def test_no_callback_is_fine(self) -> None:
        sm = make_sm(callback=None)
        sm.transition(AppState.ARMED)
        assert sm.current is AppState.ARMED

    def test_callback_fires_correct_count_for_sequence(self) -> None:
        calls: list[tuple] = []
        sm = make_sm(callback=lambda old, new, reason: calls.append((old, new, reason)))

        for state in [
            AppState.ARMED,
            AppState.RECORDING,
            AppState.SAVING,
            AppState.IDLE,
        ]:
            sm.transition(state)

        assert len(calls) == 4


# ---------------------------------------------------------------------------
# Thread enforcement
# ---------------------------------------------------------------------------


class TestThreadEnforcement:
    def test_transition_from_wrong_thread_raises(self) -> None:
        """With enforce_thread=True, calling transition() from a worker raises."""
        import threading

        sm = StateMachine(enforce_thread=True)  # created on this thread

        errors: list[Exception] = []

        def worker() -> None:
            try:
                sm.transition(AppState.ARMED)
            except WrongThreadError as exc:
                errors.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert len(errors) == 1
        assert isinstance(errors[0], WrongThreadError)

    def test_transition_from_owner_thread_succeeds(self) -> None:
        """With enforce_thread=True, calling transition() from the owner is fine."""
        sm = StateMachine(enforce_thread=True)
        sm.transition(AppState.ARMED)
        assert sm.current is AppState.ARMED
