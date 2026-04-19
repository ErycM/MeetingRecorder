"""
MeetingRecorder explicit state machine (ADR-2).

States: IDLE -> ARMED -> RECORDING -> (TRANSCRIBING ->)? SAVING -> IDLE
ERROR is reachable from any state; recovery is ERROR -> IDLE via reset().

Thread-safety invariant (I-2): ALL calls to StateMachine.transition()
MUST happen on thread T1 (the Tk mainloop). The machine records its
creation thread ident and asserts on every transition attempt. Worker
threads that detect errors emit via window.after(0, orch.on_error(reason))
which performs the transition on T1.

The on_change callback is called synchronously on the transition thread
(always T1) — it is safe for the callback to touch tkinter widgets.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from enum import Enum, auto

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State and error enums
# ---------------------------------------------------------------------------


class AppState(Enum):
    """Application lifecycle states."""

    IDLE = auto()
    ARMED = auto()
    RECORDING = auto()
    TRANSCRIBING = auto()
    SAVING = auto()
    ERROR = auto()


class ErrorReason(Enum):
    """Sources of the ERROR state (ADR-2)."""

    LEMONADE_UNREACHABLE = auto()
    MODEL_NOT_NPU = auto()
    WASAPI_DEVICE_LOST = auto()
    SINGLE_INSTANCE_CONTENTION = auto()


# ---------------------------------------------------------------------------
# Legal transition table
# ---------------------------------------------------------------------------

# Map: from_state -> set of allowed destination states
# ERROR is always reachable (added dynamically in StateMachine.transition).
# ERROR -> IDLE is the only recovery path, exposed via reset().
LEGAL_TRANSITIONS: dict[AppState, set[AppState]] = {
    AppState.IDLE: {AppState.ARMED},
    AppState.ARMED: {AppState.RECORDING, AppState.IDLE},
    AppState.RECORDING: {AppState.TRANSCRIBING, AppState.SAVING},
    AppState.TRANSCRIBING: {AppState.SAVING},
    AppState.SAVING: {AppState.IDLE},
    AppState.ERROR: {AppState.IDLE},  # recovery only via reset()
}


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class IllegalTransition(Exception):
    """Raised when a transition is not in LEGAL_TRANSITIONS."""

    def __init__(
        self,
        from_state: AppState,
        to_state: AppState,
        thread_ident: int | None = None,
    ) -> None:
        tid = thread_ident or threading.get_ident()
        super().__init__(
            f"Illegal transition {from_state.name} -> {to_state.name} "
            f"(thread ident={tid})"
        )
        self.from_state = from_state
        self.to_state = to_state


class WrongThreadError(RuntimeError):
    """Raised when transition() is called from a thread other than the owner."""

    def __init__(self, owner_tid: int, caller_tid: int) -> None:
        super().__init__(
            f"StateMachine.transition() called from thread {caller_tid}, "
            f"but machine was created on thread {owner_tid}. "
            "Dispatch via window.after(0, ...) from worker threads."
        )


# ---------------------------------------------------------------------------
# StateMachine
# ---------------------------------------------------------------------------


class StateMachine:
    """Explicit state machine for MeetingRecorder lifecycle.

    Usage::

        sm = StateMachine(on_change=my_callback)
        sm.transition(AppState.ARMED)
        sm.reset()  # returns ERROR -> IDLE

    Parameters
    ----------
    on_change:
        Called synchronously after each successful transition with signature
        ``on_change(old: AppState, new: AppState, reason: ErrorReason | None)``.
        Always invoked on the owning thread (T1 in production).
    enforce_thread:
        When True (default), assert that transition() is called on the thread
        that constructed this StateMachine. Set False only in unit tests that
        deliberately run transitions off-thread.
    """

    def __init__(
        self,
        on_change: Callable[[AppState, AppState, ErrorReason | None], None]
        | None = None,
        *,
        enforce_thread: bool = True,
    ) -> None:
        self.current: AppState = AppState.IDLE
        self._on_change = on_change
        self._owner_tid: int = threading.get_ident()
        self._enforce_thread = enforce_thread

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transition(
        self,
        to: AppState,
        *,
        reason: ErrorReason | None = None,
    ) -> None:
        """Advance the machine to *to*.

        Parameters
        ----------
        to:
            Target state. ERROR is always legal regardless of current state.
        reason:
            Must be provided when *to* is ERROR; ignored otherwise.

        Raises
        ------
        WrongThreadError
            If called from a thread other than the owning thread (when
            enforce_thread is True).
        IllegalTransition
            If the transition is not in LEGAL_TRANSITIONS.
        """
        if self._enforce_thread:
            caller_tid = threading.get_ident()
            if caller_tid != self._owner_tid:
                raise WrongThreadError(self._owner_tid, caller_tid)

        old = self.current

        # ERROR is always a legal destination from any non-ERROR state,
        # and also self-transition ERROR -> ERROR is blocked intentionally.
        if to is AppState.ERROR and old is not AppState.ERROR:
            self._apply(old, to, reason)
            return

        allowed = LEGAL_TRANSITIONS.get(old, set())
        if to not in allowed:
            raise IllegalTransition(old, to)

        self._apply(old, to, reason)

    def reset(self) -> None:
        """Recover from ERROR state back to IDLE.

        Equivalent to ``transition(AppState.IDLE)`` but only legal when
        current state is ERROR. Raises IllegalTransition otherwise.
        """
        if self.current is not AppState.ERROR:
            raise IllegalTransition(self.current, AppState.IDLE)
        self._apply(AppState.ERROR, AppState.IDLE, None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply(
        self,
        old: AppState,
        new: AppState,
        reason: ErrorReason | None,
    ) -> None:
        self.current = new
        log.debug(
            "[STATE] %s -> %s%s",
            old.name,
            new.name,
            f" ({reason.name})" if reason else "",
        )
        if self._on_change is not None:
            self._on_change(old, new, reason)
