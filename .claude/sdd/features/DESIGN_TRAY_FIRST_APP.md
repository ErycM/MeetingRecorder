# DESIGN: TRAY_FIRST_APP

> Boot MeetingRecorder as a tray utility: always-hidden CTk root, config-gated window open via a standalone readiness predicate, three pystray `Icon.notify()` toasts (started / saved / error) gated by three persisted `[notifications]` toggles. No `--autostart` flag, no new runtime dependencies, no changes to the installer or the self-exclusion chain.

**Source docs:**
- [`BRAINSTORM_TRAY_FIRST_APP.md`](./BRAINSTORM_TRAY_FIRST_APP.md)
- [`DEFINE_TRAY_FIRST_APP.md`](./DEFINE_TRAY_FIRST_APP.md)

**Approach:** A — orchestrator gate + readiness predicate + `pystray.Icon.notify()` + Settings toggles. Locked in DEFINE §Approach; not reopened here.

**Branch target:** `feat/tray-first-app` (new branch off `main`).

---

## Pre-design factual corrections (grounded in code)

Grounding the DEFINE against the live tree surfaced five things the build agent must honour:

1. **`install_startup.py` no longer exists** (retired by `EXE_PACKAGING`). DEFINE §Dependencies anticipated this — confirmed: `{userstartup}` in [`installer.iss:71`](../../../installer.iss) already delivers tray-only launch. Nothing to change in startup registration.
2. **TrayService already has `notify(title, body, on_click=None)`** at [`src/app/services/tray.py:206-263`](../../../src/app/services/tray.py). It queues toasts before `NIM_ADD` completes (icon-ready Event) so first-boot timing is a solved problem. No new wrapper needed. The `on_click` parameter stores the callback and consumes it on the next tray left-click.
3. **Orchestrator already calls `TrayService.notify` at two sites** — [`orchestrator.py:550`](../../../src/app/orchestrator.py) for "Recording started" (FR1-3) and [`orchestrator.py:775`](../../../src/app/orchestrator.py) for "Saved → …". The design gates these through a new `_notify_if_enabled` helper and adds error-toast emissions. We are NOT adding a new notification subsystem.
4. **`_TOAST_BODY_SAVED` already uses basename only** — the call site at line 777 passes `md_path.name`. Critical Rule 5 is already honoured in the saved-toast; the design codifies the invariant for the error-toast and documents the saved-toast preservation.
5. **Config field is `transcript_dir: Path | None`** (not `vault_dir`). DEFINE §Scope names it correctly (`transcript_dir`); the readiness predicate operates on this field and on `whisper_model: str`. Legacy TOML `vault_dir` keys are migrated in `config.load()` at [`config.py:171-180`](../../../src/app/config.py) — readiness sees the migrated value on the dataclass, not the raw TOML.

---

## 1. Architecture

### 1.1 Boot flow (tray-first launch — happy path)

```text
Windows login
  │
  │  {userstartup} shortcut fires (installer) OR user runs `python src/main.py`
  ▼
MeetingRecorder.exe / pythonw.exe src/main.py   (T0 = startup thread)
  │
  ├─► ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MeetingRecorder.App")
  │
  ├─► SingleInstance.acquire()
  │     └─► _write_lockfile()  writes "<pid>\n<exe_basename>\n" to %TEMP%\MeetingRecorder.lock
  │         (single_instance.py:60-62 already returns "MeetingRecorder.exe" when frozen)
  │         [VERIFY-ONLY for this feature — no code change — Critical Rule #4]
  │
  ├─► Config.load() reads %APPDATA%\MeetingRecorder\config.toml
  │     └─► NEW: parses data.get("notifications", {}) → notify_started / notify_saved / notify_error
  │         (all default True when section missing — backward compat)
  │
  ├─► theme.init()  (must run before any CTk widget)
  │
  └─► Orchestrator(cfg).run()                 (T0 becomes T1 once mainloop starts)
         │
         ├─► AppWindow(...)                   (CTk root constructed, starts WITHDRAWN — ADR-4)
         │     └─► self._root.withdraw() called in __init__ BEFORE control returns
         │
         ├─► CaptionRouter / Services wired (TranscriptionService, RecordingService,
         │   MicWatcher, TrayService)         — unchanged from today
         │
         ├─► _mic_watcher.start()             (T2 — polling CapabilityAccessManager)
         ├─► _tray_svc.start()                (T9 — pystray loop, NIM_ADD pending)
         │
         ├─► NPU check thread                 (T6 — non-blocking; does NOT gate boot)
         │
         ├─► **NEW**: readiness-gate branch   (runs on T0/T1 before mainloop)
         │     │
         │     ok, reason = is_ready(self._config)
         │     │
         │     ├── ok=True  ──►  STAY HIDDEN
         │     │                  (tray icon visible, no window; CTk root still alive)
         │     │
         │     └── ok=False ──►  self._window.show()                (deiconify)
         │                        self._window.switch_tab("Settings")
         │                        log.info "[ORCH] Readiness failed: %s" % reason
         │
         └─► self._window.run()               (blocks on Tk mainloop)
               │
               └─► T1 services after()-dispatch callbacks from T2/T6/T9
                   even while root is withdrawn (CTk mainloop unaffected — G6)
```

### 1.2 Runtime notification flow (three categories × three toggles)

```text
                                 ┌─────────────────────────┐
                                 │  Orchestrator on T1     │
                                 │  _notify_if_enabled(...)│   ← single choke point
                                 │                         │     (toggle + log + dispatch)
                                 │  category ∈ {started,   │
                                 │             saved,      │
                                 │             error}      │
                                 └─────────┬───────────────┘
                                           │ reads self._config.notify_<category>
                                           │
                        ┌──────────────────┼──────────────────┐
                        │                  │                  │
                     True (on)          False (off)        always
                        │                  │                  │
                        ▼                  ▼                  ▼
          ┌─────────────────────┐   ┌──────────────┐   ┌──────────────────┐
          │ TrayService.notify( │   │ no-op for    │   │ log.info(        │
          │   title, body,      │   │ TrayService; │   │   "[ORCH] ...")  │
          │   on_click=...)     │   │ INFO log     │   │  ALWAYS emitted  │
          └─────────┬───────────┘   │ still fires  │   │  (SC6)           │
                    │               └──────────────┘   └──────────────────┘
                    ▼
          T9 pystray thread
          (queued if NIM_ADD pending)
                    │
                    ▼
          Shell_NotifyIconW(NIF_INFO)
          (Win11 balloon-tip toast)
```

### 1.3 Thread layout (unchanged by this feature — confirmed)

| Thread | Role |
|---|---|
| T0 | Startup — `main()` until `_window.run()` enters mainloop |
| T1 | Tk mainloop — ALL UI + `StateMachine.transition()` + `_notify_if_enabled` |
| T2 | MicWatcher `_poll_loop` (registry) — dispatches to T1 via `window.dispatch()` |
| T5 | RecordingService WASAPI capture worker (inside `DualAudioRecorder`) |
| T6 | Transcription / NPU check / batch transcribe / save workers |
| T9 | pystray event loop (tray menu callbacks + `Icon.notify` Shell call) |

No new threads, no new cross-thread edges. See §4 for the exhaustive boundary list.

---

## 2. File manifest (ordered by dependency)

Strict dependency order: each file is created/modified before any consumer below it. No circular dependencies.

| # | File | Action | SCs covered |
|---|------|--------|-------------|
| 1 | `src/app/readiness.py` | **NEW** | SC2 |
| 2 | `src/app/config.py` | **MODIFY** | SC2, SC6 |
| 3 | `src/app/services/tray.py` | **VERIFY** (no code change) | SC3, SC4, SC5 |
| 4 | `src/app/single_instance.py` | **VERIFY** (no code change) | SC7, SC8 |
| 5 | `src/app/services/mic_watcher.py` | **VERIFY** (no code change) | SC8 |
| 6 | `src/ui/app_window.py` | **VERIFY** (no code change) | SC1, SC7, SC10 |
| 7 | `src/app/orchestrator.py` | **MODIFY** | SC1, SC2, SC3, SC4, SC5, SC6 |
| 8 | `src/ui/settings_tab.py` | **MODIFY** | SC6 |
| 9 | `installer.iss` / startup shortcut | **VERIFY** (no code change) | SC9 |
| 10 | `tests/test_readiness.py` | **NEW** | SC2 |
| 11 | `tests/test_config.py` | **MODIFY** | SC6 (config round-trip) |
| 12 | `tests/test_orchestrator_tray_first.py` | **NEW** (Windows-only) | SC1, SC2, SC3, SC6 |
| 13 | `tests/test_app_window_hidden_mainloop.py` | **NEW** | SC10 |
| 14 | `tests/test_mic_watcher.py` / `tests/test_self_exclusion_frozen.py` | **VERIFY** (no code change) | SC8 |

### File details

Each entry below: absolute purpose, depends on, key changes (inline snippets for load-bearing edits only).

---

#### 1. `src/app/readiness.py` — **NEW**

**Purpose.** Pure function that decides "can the app record right now, purely from the persisted config?". Standalone module (not a method on `Config`) so unit tests import it without constructing a Config lifecycle — matches `src/app/npu_guard.py` idiom (ADR-2).

**Depends on.** `src/app/config.py` (reads `Config.transcript_dir` and `Config.whisper_model`).

**Key changes — new file:**

```python
"""
Readiness predicate — can the app record right now?

Pure function. No I/O except Path.exists(), Path.is_dir(), and a single
tempfile write-probe inside transcript_dir. Does NOT probe Lemonade
(cold-start is slow; Lemonade errors surface via the existing ERROR
state + notify_error toast — see DESIGN §1.2).

The reason strings below are part of the module contract — SC2 asserts
on exact equality.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

REASON_TRANSCRIPT_DIR_UNSET = "Transcript directory not set"
REASON_TRANSCRIPT_DIR_MISSING = "Transcript directory does not exist: {path}"
REASON_TRANSCRIPT_DIR_NOT_WRITABLE = "Transcript directory is not writable: {path}"
REASON_WHISPER_MODEL_EMPTY = "Whisper model is empty"


def is_ready(config: object) -> tuple[bool, str]:
    """Return (True, "") when the app can record; else (False, reason)."""
    transcript_dir: Path | None = getattr(config, "transcript_dir", None)
    whisper_model: str = getattr(config, "whisper_model", "") or ""

    if transcript_dir is None or str(transcript_dir).strip() == "":
        return False, REASON_TRANSCRIPT_DIR_UNSET

    # Coerce to Path for any string sneaking through legacy TOML
    tpath = Path(transcript_dir)

    if not tpath.exists() or not tpath.is_dir():
        return False, REASON_TRANSCRIPT_DIR_MISSING.format(path=tpath)

    if not _is_writable(tpath):
        return False, REASON_TRANSCRIPT_DIR_NOT_WRITABLE.format(path=tpath)

    if not whisper_model.strip():
        return False, REASON_WHISPER_MODEL_EMPTY

    return True, ""


def _is_writable(directory: Path) -> bool:
    """Best-effort writability check via tempfile.NamedTemporaryFile.

    Uses dir= so the sentinel lands inside the target directory; delete=True
    so a successful probe leaves no trace. PermissionError / OSError → False.
    """
    try:
        with tempfile.NamedTemporaryFile(
            dir=str(directory), prefix=".mr_ready_", delete=True
        ):
            return True
    except (PermissionError, OSError) as exc:
        log.debug("[READINESS] Writability probe failed in %s: %s", directory, exc)
        return False
```

**Why these checks and not more:**
- `wav_dir` is excluded because `orchestrator.py:53` falls back to `_DEFAULT_WAV_DIR` under `%APPDATA%` when unset — an unset `wav_dir` is NOT a failure. DEFINE §Scope explicitly excludes it.
- `obsidian_vault_root` is excluded because it is used only for `obsidian://` URI construction; the recorder runs fine without it.
- Lemonade reachability is excluded (DEFINE risk #4) — 30 s cold start would block boot.
- `silence_timeout`, `lemonade_base_url`, `live_captions_enabled`, device indices all have safe defaults validated in `Config.__post_init__`; readiness never rejects them.

---

#### 2. `src/app/config.py` — **MODIFY**

**Purpose.** Add three bool fields for the new `[notifications]` TOML section with defaults `True`, round-trip them through `load()` / `save()`, and preserve backward compatibility with pre-feature `config.toml` files (no `[notifications]` key present → all three default to `True`).

**Depends on.** Nothing new.

**Key changes:**

- **Dataclass fields.** Add after `lemonade_base_url` (line 123):
  ```python
  # Notification toggles — [notifications] TOML table. Defaults all True
  # so first-launch users hear every event; can be flipped off in Settings
  # if toasts feel noisy. Backward-compat: section missing → all True
  # (implemented in load()).
  notify_started: bool = True
  notify_saved: bool = True
  notify_error: bool = True
  ```
- **`__post_init__` validation.** Extend with strict bool checks (mirrors `_coerce_optional_int` pattern):
  ```python
  for name in ("notify_started", "notify_saved", "notify_error"):
      val = getattr(self, name)
      if not isinstance(val, bool):
          raise ConfigError(f"{name} must be bool, got {type(val).__name__}")
  ```
- **`load()`** — add after the existing field reads (near line 205):
  ```python
  notif = data.get("notifications", {}) or {}
  notify_started = bool(notif.get("notify_started", True))
  notify_saved = bool(notif.get("notify_saved", True))
  notify_error = bool(notif.get("notify_error", True))
  ```
  Pass as kwargs to `Config(...)`. Backward-compat: when the TOML file has no `[notifications]` section, `data.get("notifications", {})` returns `{}`, every sub-get returns the default `True`.
- **`save()`** — add after the `lemonade_base_url` write (near line 245):
  ```python
  data["notifications"] = {
      "notify_started": cfg.notify_started,
      "notify_saved": cfg.notify_saved,
      "notify_error": cfg.notify_error,
  }
  ```
  `tomli_w.dumps()` renders this as `[notifications]` with three boolean keys. **Decision (ADR-2):** nested table keyed by `notifications`, flat keys inside — NOT inline table syntax. Nested form round-trips cleanly through `tomli_w` and matches the project's existing TOML style.

---

#### 3. `src/app/services/tray.py` — **VERIFY** (no code change)

**Purpose.** Confirm `notify(title, body, on_click=None)` exists with the expected shape; confirm Quit menu cleans up correctly; confirm queueing handles pre-`NIM_ADD` calls.

**Key findings (cited from source):**
- `notify(title, body, on_click=None)` at [`tray.py:206-263`](../../../src/app/services/tray.py) — signature matches DEFINE §Out Q10 (minimal, `on_click` present).
- `_queued_notifications` + `_icon_ready` Event at [`tray.py:115-116`](../../../src/app/services/tray.py) handles the pre-`NIM_ADD` race — exactly what the tray-first path needs for a recording that fires within milliseconds of boot.
- Quit menu item at [`tray.py:329-332`](../../../src/app/services/tray.py) dispatches `self._on_quit` through the provided `dispatch` callable then calls `icon.stop()`. Orchestrator's `_on_quit` at [`orchestrator.py:920-953`](../../../src/app/orchestrator.py) already stops all services and calls `self._window.quit()`, which triggers `main.py`'s `finally: guard.release()` — the lockfile is removed. Shutdown order is correct as-is.
- `notify()` runs on the caller's thread (T1 from orchestrator), updates `self._pending_toast_click` and puts a tuple in `_queued_notifications` or calls `self._icon.notify(body, title)` directly. The pystray `Icon.notify` method calls `Shell_NotifyIconW(NIM_MODIFY, NIF_INFO, ...)` on the tray thread internally — it is documented thread-safe in pystray ≥ 0.19, used from T1 today without issue.

**No code change required.** The build agent MUST NOT refactor tray.py as part of this feature.

---

#### 4. `src/app/single_instance.py` — **VERIFY** (no code change)

**Purpose.** Confirm the lockfile chain survives the boot-order change.

**Key findings (cited from source):**
- `acquire()` at [`single_instance.py:100-118`](../../../src/app/single_instance.py) writes the lockfile before returning True. Called from `main.py:64` BEFORE `Orchestrator(cfg).run()`. Order matters: mic-watcher thread starts inside `orchestrator.run()` — at that point the lockfile is already on disk. No reorder.
- `_exe_basename()` at [`single_instance.py:53-62`](../../../src/app/single_instance.py) returns `"MeetingRecorder.exe"` when `sys.frozen`, else `os.path.basename(sys.executable)`. SC8 regression guard already exists at `tests/test_self_exclusion_frozen.py`.
- `release()` at [`single_instance.py:120-128`](../../../src/app/single_instance.py) is idempotent and called from `main.py:87` in a `finally:` block. Quit-through-tray triggers `orchestrator._on_quit()` → `window.quit()` → mainloop exits → control returns to `main()` → `finally: guard.release()`. Chain preserved.

**No code change.**

---

#### 5. `src/app/services/mic_watcher.py` — **VERIFY** (no code change)

**Purpose.** Confirm `_read_lockfile_exclusion()` still works when the process is `MeetingRecorder.exe`, and source-run `python.exe`/`pythonw.exe` aliasing still passes.

**Key findings (cited from source):**
- `_is_self()` at [`mic_watcher.py:112-154`](../../../src/app/services/mic_watcher.py) uses exact last-segment basename match (case-insensitive) plus the `python.exe ↔ pythonw.exe` alias set. Covered by `tests/test_self_exclusion_frozen.py` (frozen case) and existing aliasing tests in `tests/test_mic_watcher.py`.
- Orchestrator reads the lockfile via `_read_lockfile_exclusion()` at [`orchestrator.py:131-148`](../../../src/app/orchestrator.py) and passes the result to `MicWatcher(self_exclusion=...)` at [`orchestrator.py:322-328`](../../../src/app/orchestrator.py). Tray-first boot does NOT change this sequence; the lockfile is written by `SingleInstance.acquire()` before orchestrator construction, so the read sees the correct basename.

**No code change.** SC8's regression test (§5) pins this invariant against any future refactor.

---

#### 6. `src/ui/app_window.py` — **VERIFY** (no code change)

**Purpose.** Confirm three things the tray-first boot depends on:
1. CTk root is WITHDRAWN on construction (so the mainloop is alive but no visible window).
2. `WM_DELETE_WINDOW` routes to `self.hide` (withdraw), not `destroy` — close-to-tray already wired.
3. `switch_tab(name)` exists and can target "Settings".

**Key findings (cited from source):**
- Constructor at [`app_window.py:88-92`](../../../src/ui/app_window.py) creates `self._root = ctk.CTk()`, sets title, geometry — **but does NOT call `deiconify()`**. CTk roots are visible by default once constructed. The current boot flow relies on `orchestrator.run()` calling `self._window.show()` at line 360 to make it visible.

  **Finding:** on the current tree, the root is ALREADY VISIBLE at the end of `AppWindow.__init__` because CTk.CTk() creates a top-level window. The tray-first change must therefore actively withdraw it. **This is an implementation choice, not a new method — see ADR-4 below.** The build agent adds `self._root.withdraw()` as the LAST line of `AppWindow.__init__` (immediately before the debug log) so the root is guaranteed-hidden on return from the constructor. `show()` still works (already calls `deiconify()` + `lift()` + `focus_force()` at line 178-181).

  **Amendment to #6's status:** this file now becomes **MODIFY (one line)** — add `self._root.withdraw()` at the end of `__init__`. This is the minimum surface-area change; the alternative (not instantiating the window until needed) was rejected in ADR-4.
- `WM_DELETE_WINDOW` at [`app_window.py:95`](../../../src/ui/app_window.py) is wired to `self.hide` which calls `self._root.withdraw()`. SC7 already passes; no change.
- `switch_tab(name)` at [`app_window.py:170-175`](../../../src/ui/app_window.py) calls `self._tabview.set(name)` inside a try/except. Callers: `orchestrator.py:570` already uses it for the recording-started toast-click. Targeting `"Settings"` works — `AppWindow.__init__` creates tabs with the exact names `"Live"`, `"History"`, `"Settings"` at lines 101-103.

**Revised status: MODIFY (single line add).** See corrected entry in the file manifest.

Corrected manifest row for #6:

| # | File | Action | SCs covered |
|---|------|--------|-------------|
| 6 | `src/ui/app_window.py` | **MODIFY (1 line)** | SC1, SC7, SC10 |

---

#### 7. `src/app/orchestrator.py` — **MODIFY** (primary file of this feature)

**Purpose.** Replace the unconditional `self._window.show()` with a readiness-gated branch; route all three toast categories through a new `_notify_if_enabled` helper; add error-toast emissions at the four documented sites.

**Depends on.** #1 (`readiness.is_ready`), #2 (new Config fields), #6 (withdrawn CTk root).

**Key changes — seven discrete edits:**

**(a) Import readiness module** — top of file (after line 40):
```python
# Readiness predicate — decides whether tray-first boot opens the window.
# Imported at module level (pure Python, no I/O) so orchestrator tests
# can mock it without a config round-trip.
from app.readiness import is_ready as _is_ready  # noqa: E402
```

**(b) New helper `_notify_if_enabled`** — add as private method on `Orchestrator` (near the other notification sites, after `_on_toast_clicked` around line 575). Single choke point for all toast emissions:
```python
def _notify_if_enabled(
    self,
    category: str,
    title: str,
    body: str,
    *,
    on_click: "Callable[[], None] | None" = None,
) -> None:
    """Emit a tray toast iff the matching Config toggle is True.

    Always emits an INFO log regardless of the toggle (SC6).
    ``category`` must be one of: ``"started"``, ``"saved"``, ``"error"``.

    Must be called from T1 (the Tk mainloop). Body is truncated to
    60 characters to match NFR6; title is used as-is (already short).
    """
    body_trimmed = body[:60] if body else body
    log.info("[ORCH] notify.%s: %s", category, body_trimmed)

    attr = f"notify_{category}"
    enabled = bool(getattr(self._config, attr, True))
    if not enabled:
        return

    try:
        self._tray_svc.notify(  # type: ignore[attr-defined]
            title, body_trimmed, on_click=on_click
        )
    except Exception as exc:
        log.warning(
            "[ORCH] tray.notify(%s) failed (non-fatal): %s", category, exc
        )
```
**Invariant:** every toast emission in the orchestrator goes through this helper. Direct `self._tray_svc.notify(...)` calls are banned for new code.

**(c) Replace the "Recording started" call site** at [`orchestrator.py:549-556`](../../../src/app/orchestrator.py):
```python
# FR1-FR3 → _notify_if_enabled gated on config.notify_started (SC3, SC6).
self._notify_if_enabled(
    "started",
    _TOAST_TITLE,
    _TOAST_BODY_RECORDING,
    on_click=self._on_toast_clicked,
)
```

**(d) Replace the "Saved" call site** at [`orchestrator.py:774-780`](../../../src/app/orchestrator.py):
```python
# FR4 → _notify_if_enabled gated on config.notify_saved (SC4, SC6).
# md_path.name is basename-only — Critical Rule #5 already honoured.
self._notify_if_enabled(
    "saved",
    _TOAST_TITLE,
    _TOAST_BODY_SAVED.format(name=md_path.name),
)
```

**(e) Four new error-toast emission sites** (SC5 — must cover every DEFINE-named failure path):

1. In `_batch_transcribe_and_save` exception branch near [`orchestrator.py:727-734`](../../../src/app/orchestrator.py) — replace the existing `_publish_save_result` (which drives the LiveTab banner, still wanted) with an ADDITIONAL `_notify_if_enabled("error", ...)`. Both are emitted; the banner is in-window, the toast is OS-level. Kept together because the reason string is already computed here.
2. In `_on_npu_failed` at [`orchestrator.py:424-448`](../../../src/app/orchestrator.py) — add a single `_notify_if_enabled("error", _TOAST_TITLE, f"NPU not ready: {error[:40]}")` after the SettingsTab NPU-status set.
3. In `_on_service_error` at [`orchestrator.py:1145-1151`](../../../src/app/orchestrator.py) — add `_notify_if_enabled("error", _TOAST_TITLE, f"Service error: {str(exc)[:40]}")` before the state-machine transition.
4. In `_transition_to_armed` silent-capture safety-net branch at [`orchestrator.py:822-838`](../../../src/app/orchestrator.py) — add `_notify_if_enabled("error", _TOAST_TITLE, "Capture issue — check audio settings")` right after `self._capture_warning_active = True`.

All four paths already log + surface the issue via banner/state machine. The toast is additive.

**(f) Replace the unconditional `self._window.show()` at [`orchestrator.py:359-360`](../../../src/app/orchestrator.py) with the readiness gate** — the single behavioural change that makes the app tray-first:
```python
# Tray-first boot: open the window ONLY if the readiness predicate fails.
# When it passes (the common case), the CTk root stays withdrawn and the
# user only sees the tray icon — DEFINE G1/G2/SC1/SC2.
ok, reason = _is_ready(self._config)
if not ok:
    log.info("[ORCH] Readiness failed — opening Settings: %s", reason)
    self._window.show()  # type: ignore[attr-defined]
    self._window.switch_tab("Settings")  # type: ignore[attr-defined]
else:
    log.info("[ORCH] Readiness OK — staying in tray")
# NOTE: self._window.run() below enters mainloop even when the root is
# withdrawn (G6). Do NOT move it.
```

**(g) Quit path — no new code.** Orchestrator's existing `_on_quit` at [`orchestrator.py:920-953`](../../../src/app/orchestrator.py) correctly stops RecordingService first, then TranscriptionService, MicWatcher, TrayService, and finally calls `self._window.quit()`. Control returns to `main.py:87` where `guard.release()` removes the lockfile. SC7 shutdown order is already correct.

---

#### 8. `src/ui/settings_tab.py` — **MODIFY**

**Purpose.** Add three `CTkSwitch` widgets bound to the new Config fields; include them in the Config rebuild inside `_on_save_clicked`.

**Depends on.** #2 (new Config fields).

**Key changes:**

- **New section header + three switches.** Insert a new `Notifications` section between `Behavior` and `Storage` (around [`settings_tab.py:263`](../../../src/ui/settings_tab.py), after the `Launch on login` switch, before `_hdr("Storage")`):
  ```python
  # ----------------------------------------------------------------
  # Section: Notifications (FR-new — three toggles matching [notifications])
  # ----------------------------------------------------------------
  _hdr("Notifications")

  _lbl("Notify on recording start:")
  self._notify_started_var = tk.BooleanVar(value=config.notify_started)
  ctk.CTkSwitch(
      scroll_frame,
      text="",
      variable=self._notify_started_var,
      onvalue=True,
      offvalue=False,
  ).grid(row=row, column=1, sticky="w", pady=4)
  row += 1

  _lbl("Notify on transcript saved:")
  self._notify_saved_var = tk.BooleanVar(value=config.notify_saved)
  ctk.CTkSwitch(
      scroll_frame, text="",
      variable=self._notify_saved_var,
      onvalue=True, offvalue=False,
  ).grid(row=row, column=1, sticky="w", pady=4)
  row += 1

  _lbl("Notify on error:")
  self._notify_error_var = tk.BooleanVar(value=config.notify_error)
  ctk.CTkSwitch(
      scroll_frame, text="",
      variable=self._notify_error_var,
      onvalue=True, offvalue=False,
  ).grid(row=row, column=1, sticky="w", pady=4)
  row += 1
  ```
- **Config rebuild in `_on_save_clicked`** around [`settings_tab.py:513-528`](../../../src/ui/settings_tab.py) — add three kwargs to the `Config(...)` call:
  ```python
  notify_started=self._notify_started_var.get(),
  notify_saved=self._notify_saved_var.get(),
  notify_error=self._notify_error_var.get(),
  ```
- **No new validation.** Bools from `BooleanVar` are guaranteed bools — Config's `__post_init__` rejects non-bool only for defence-in-depth against future TOML edits.
- **Save semantics** — DEFINE §Open questions (Settings-tab-to-Live-tab switch after fix) resolved here: when the user fixes `transcript_dir` and clicks Save, we LEAVE the window open. The user may want to review other Settings; the next launch will boot tray-only if readiness passes. No auto-hide in `_on_save_clicked`.

---

#### 9. `installer.iss` / startup shortcut — **VERIFY** (no code change)

**Purpose.** Confirm `{userstartup}\MeetingRecorder.lnk` at [`installer.iss:71`](../../../installer.iss) already delivers tray-only launch with no flags. DEFINE G8 claim.

**Key findings:**
- `[Icons]` entry at line 71 creates `{userstartup}\MeetingRecorder` pointing at `{app}\MeetingRecorder.exe` with the AppUserModelID set. No `Parameters:` — the EXE is invoked with zero args, which is what we want (no `--autostart` flag per DEFINE G1).
- The task is `unchecked` by default (line 57) — users opt in during install. Signed-in launch produces exactly the same behaviour as a manual Start-Menu run, because `main.py` has a single code path with no flag branching.

**No code change.** SC9 verification is manual (real install → sign out/in).

---

## 3. ADRs (decisions + rejections)

Each ADR: Decision / Rationale / Rejected alternatives / Consequences.

### ADR-1 — pystray `Icon.notify()` as the sole toast channel

**Decision.** Use `pystray.Icon.notify(body, title)` (already exposed via `TrayService.notify`) for all three toast categories. No new library, no new PyInstaller hidden-imports.

**Rationale.** Zero-runtime-cost: pystray is already pinned, shipped, and battle-tested in the current build (the recording-started and saved toasts already use it). The queueing logic for pre-`NIM_ADD` delivery is already implemented at [`tray.py:251-257`](../../../src/app/services/tray.py) — the tray-first boot path (where a recording could fire within milliseconds of launch) is already covered.

**Rejected alternatives.**
- `winotify` — new runtime dep, PyInstaller hidden-import tuning, `--collect-all winotify` per installer reports, AUMID registration worries. DEFINE §Out explicitly defers this.
- Custom `CTkToplevel` borderless toast — re-implements a Windows built-in, no Action Center persistence, inferior on multi-monitor configs. BRAINSTORM §Approach C rejected.
- `win32gui.Shell_NotifyIconW(NIF_INFO)` direct — bypasses the abstraction pystray already provides; no benefit. BRAINSTORM explicitly rejected.

**Consequences.** No clickable action buttons in v1. The `on_click` fallback (left-click-tray consumes `_pending_toast_click`) remains the only interactive path. Phase-2 upgrade to `winotify` only touches `TrayService.notify` — no Orchestrator / Config / Settings changes needed.

---

### ADR-2 — Standalone `readiness.py` over method-on-`Config`

**Decision.** Readiness is a module-level pure function in a new file `src/app/readiness.py`. It takes a `Config` and returns `tuple[bool, str]`.

**Rationale.**
- **Testability.** Unit tests import `is_ready` without constructing a full `Config` lifecycle (`_source_path`, atomic TOML save, etc.). Four failure-mode parametrized cases (DEFINE SC2) become five trivial unit tests.
- **Single-responsibility.** `Config` is a data container with round-trip logic. Domain decisions ("can we record?") belong elsewhere. This matches `src/app/npu_guard.py` which is also a separate pure module.
- **Extensibility.** Future checks (e.g. "does the WAV archive dir have > 1 GB free?") land in `readiness.py` without bloating `Config`.

**Rejected alternatives.**
- Method `Config.is_ready()` — couples dataclass to filesystem I/O; forces `__post_init__` or methods to import tempfile / Path.exists; complicates tests.
- Inline `if cfg.transcript_dir is None: ...` in `orchestrator.run()` — replicates logic; no reuse from Settings-tab on-save auto-re-check (Phase-2 possibility).

**Consequences.** One new module, one new test file. The public API is the `is_ready(config)` function and four module-level constants (`REASON_TRANSCRIPT_DIR_UNSET`, `REASON_TRANSCRIPT_DIR_MISSING`, `REASON_TRANSCRIPT_DIR_NOT_WRITABLE`, `REASON_WHISPER_MODEL_EMPTY`) — SC2 asserts against those constants so message drift is caught at test time.

---

### ADR-3 — Config-gated `self._window.show()` vs `--autostart` flag

**Decision.** Replace the unconditional `self._window.show()` with a readiness-gated branch. No command-line flag. Single code path, single behaviour.

**Rationale.** BRAINSTORM user-locked decision: "uniform always-hidden behaviour regardless of how it was launched." Two code paths for one concept is debt. The {userstartup} shortcut invokes the EXE with no arguments — if we added `--autostart`, the user running `python src/main.py` manually would get window-first behaviour while the post-login launch stays tray-first, which violates DEFINE G1.

**Rejected alternatives.**
- `--autostart` flag branched in `main.py` — dual entry points, divergent QA matrix, users confused when they run the app manually and see a window.
- Separate tray-only entry-point script (`main_tray.py`) — worse: two real files to keep in sync.

**Consequences.** No regression test for flag-parse logic (there is no flag). Boot latency measured from a single code path. Users who WANT to see the window on boot either (a) leave `transcript_dir` unset, triggering the readiness gate (perverse), or (b) rely on the existing FR34-era "window auto-shows on mic activity" semantics — which this feature explicitly undoes. Correct answer post-ship: right-click tray → Show Window.

---

### ADR-4 — Always-hidden CTk root via `self._root.withdraw()` at end of `AppWindow.__init__`

**Decision.** CTk root is constructed as today, then immediately withdrawn at the end of `AppWindow.__init__` (one-line add). `show()` and `hide()` work exactly as they do today. Mainloop is entered via `self._root.mainloop()` on T1 regardless of visibility.

**Rationale.** The Tk mainloop needs an alive root for `after(0, fn)` dispatch to work (Critical Rule 2). If we never instantiate the window, worker threads have no dispatch target and the whole threading invariant collapses. CTk's `withdraw()` keeps the root alive but invisible — verified by SC10.

**Rejected alternatives.**
- Never construct `AppWindow` until readiness fails — would require a parallel event-loop for `Orchestrator.dispatch` that bypasses AppWindow. Violates Rule 2; breaks the 15+ existing `window.dispatch(...)` call sites across services.
- Construct `AppWindow` but never call `mainloop()` — no `after()` dispatch → MicWatcher callbacks never marshal to T1 → state machine never advances. Catastrophic.
- Construct `AppWindow` visible, immediately call `hide()` in `Orchestrator.run()` — the window would flash briefly at boot, which is exactly what tray-first users don't want. Perceptibly worse UX.

**Consequences.** The one-line `self._root.withdraw()` at the end of `__init__` is the ONLY UI change. `show()` continues to `deiconify()` + `lift()` + `focus_force()` unchanged. SC10 unit test exists in `tests/test_app_window_hidden_mainloop.py` (new) that constructs an AppWindow, schedules a dispatch from a worker thread, and asserts the callback runs on T1 within 200 ms with no visible window.

---

### ADR-5 — Basename-only redaction in toast bodies

**Decision.** Saved-transcript toast body uses `md_path.name` (basename, already in use at [`orchestrator.py:777`](../../../src/app/orchestrator.py)). Error toasts truncate reason strings to 60 chars via `body[:60]`. No full paths. No transcript content.

**Rationale.** Critical Rule 5 — "Never log vault paths or transcript contents without redaction." Toasts render in the user's taskbar and in Action Center history; they're persistent personal surface area.

**Rejected alternatives.**
- Full path `str(md_path)` — leaks user's vault layout to Action Center. Violates Rule 5.
- Hashed / obfuscated path — useless to the user who wants to know which file was saved.
- Embed a `.md` preview (first 40 chars of transcript) — leaks transcript content. Violates Rule 5.

**Consequences.** `_notify_if_enabled` enforces the 60-char ceiling uniformly; callers can't accidentally leak more by passing a long body. Existing `_TOAST_BODY_SAVED = "Saved -> {name}"` template already complies.

---

### ADR-6 — Default toggle values: all ON

**Decision.** `notify_started`, `notify_saved`, `notify_error` all default `True`.

**Rationale.** DEFINE Q3 resolution — user-locked in BRAINSTORM. First-launch users who haven't touched Settings should learn what the app does via toasts. Any silencing is an informed choice.

**Rejected alternatives.**
- Opt-in (all OFF) — user installs, signs in, sees nothing, assumes broken. Silent failure is the worst UX outcome for a tray utility.
- Mixed defaults (saved + error ON, started OFF) — arbitrary; the two "informational" events (`started`, `saved`) bracket a meeting, dropping one breaks the symmetry.

**Consequences.** Users who find toasts noisy flip them off one at a time in Settings. The README should document this clearly (scoped out of this feature).

---

### ADR-7 — Toast emission routed through orchestrator, not a dedicated notifier service

**Decision.** Keep toast emission in Orchestrator via the new `_notify_if_enabled` helper. Do NOT introduce a `NotificationService` subscribing to state transitions.

**Rationale.** State-change notification is already centralised in `Orchestrator._on_state_change` and the recording-started/saved sites. Adding a separate service would require either (a) a pub/sub system (new dependency, new threading contract), or (b) a parallel callback registration — both disproportionate to three toast categories. `_notify_if_enabled` is 15 lines; a NotificationService would be 100+ and add a sixth thread boundary to audit.

**Rejected alternatives.**
- Dedicated `NotificationService` subscribing to state via `StateMachine.on_change` — new thread boundary, duplicates orchestrator's existing state-change hook.
- Free module function `notify_if_enabled(config, tray, category, ...)` — forced callers to pass four arguments every call; encapsulation lost. Private method is cleaner.

**Consequences.** Future toast categories (Phase-2 Lemonade-cold-start, first-run welcome) land as additional cases in `_notify_if_enabled` or sibling helpers inside Orchestrator. If the feature grows past 6+ categories, reconsider extracting.

---

### ADR-8 — TOML section shape: nested `[notifications]` table, not inline

**Decision.** `config.toml` renders the section as:
```toml
[notifications]
notify_started = true
notify_saved = true
notify_error = true
```
(Not inline-table form `notifications = { notify_started = true, ... }`.)

**Rationale.** `tomli_w.dumps({"notifications": {...}})` naturally emits nested-table syntax; matches the project's existing style. Inline tables are valid TOML but uncommon in this codebase.

**Rejected alternatives.**
- Inline table — equally valid, round-trips identically through tomllib; but breaks style consistency and is harder to diff in review.
- Three flat keys at root (`notify_started = true` without a section) — works, but loses the logical grouping and clutters the root namespace.

**Consequences.** The `tests/test_config.py` round-trip case asserts the rendered file contains the exact line `"[notifications]"` to pin this. Backward-compat is preserved because `data.get("notifications", {})` returns `{}` when the section is absent (pre-feature configs).

---

## 4. Threading model — exhaustive cross-thread boundary list

Critical Rule 2 says every UI touch must go through `AppWindow.dispatch()`. This feature adds zero new threads. Every cross-thread boundary it touches:

### 4.1 MicWatcher (T2) → Orchestrator (T1) → toast

**Path.** `MicWatcher._poll_loop` on T2 detects mic activity → calls `self._dispatch(self._on_mic_active)` which posts `_on_mic_active` onto T1 via `window.after(0, ...)` → T1 runs `Orchestrator._on_mic_active` → `_start_recording` → `_notify_if_enabled("started", ...)` → `self._tray_svc.notify(title, body, on_click=...)`.

**`TrayService.notify` runs on T1** (caller's thread). Internally it either appends to `_queued_notifications` (when the icon isn't NIM_ADD-registered yet) or calls `self._icon.notify(body, title)` which enqueues a Shell-API call for T9 (pystray's internal thread). **pystray's Icon.notify is documented thread-safe.**

**Rule 2 impact.** No Tk touch on T2. `_notify_if_enabled` runs on T1 → OK. Log output is thread-safe (standard logging module). No issue.

### 4.2 RecordingService (T5 / T6) → Orchestrator (T1) → toast

**Path.** `_batch_transcribe_and_save` runs on a save/transcribe worker thread (T6). On success, it calls `self._window.dispatch(lambda: self._on_save_complete(md_path, archived_wav, duration_s))` — crosses to T1. `_on_save_complete` invokes `_notify_if_enabled("saved", ...)` on T1.

On failure (except branch), the worker calls `self._window.dispatch(lambda r=reason: self._publish_save_result(ToastKind.ERROR, f"Save failed: {r}"))` and `self._window.dispatch(self._transition_to_armed)` — both targets run on T1. The new error-toast emission is wired INSIDE those T1-side handlers (not in the worker), so `_notify_if_enabled("error", ...)` runs on T1 → goes through the same choke point.

**Rule 2 impact.** None — all toast calls happen on T1 post-dispatch.

### 4.3 Tray left-click callback (T9) → AppWindow.show (T1)

**Path.** User left-clicks tray icon → pystray fires `_show_window(icon, item)` on T9 → `_dispatch(self._on_show_window)` which is orchestrator-supplied `lambda: dispatch(self._window.show)` at [`orchestrator.py:333`](../../../src/app/orchestrator.py) → `window.after(0, window.show)` → `show()` runs on T1 → `deiconify()` on the CTk root.

**Rule 2 impact.** Already correct today. The recording-started toast-click path (`_on_toast_clicked` at [`orchestrator.py:558-574`](../../../src/app/orchestrator.py)) dispatches a two-step closure (show + switch_tab). This feature does NOT change that path.

### 4.4 Tray Quit (T9) → Orchestrator shutdown → SingleInstance release

**Order, explicit:**

1. User clicks `Quit` in tray menu → pystray invokes `_quit(icon, item)` on T9.
2. `_quit` calls `self._dispatch(self._on_quit)` — posts `Orchestrator._on_quit` to T1 via `window.after(0, ...)`.
3. T9 then immediately calls `icon.stop()` — marks the pystray loop for shutdown.
4. T1 runs `_on_quit`: stops RecordingService (`set_stream_sink(None)` then `.stop()`), closes TranscriptionService (best-effort), stops MicWatcher (`.stop()` joins T2 with 2s timeout), stops TrayService (`.stop()` joins T9 with 3s timeout), unregisters hotkey, calls `self._window.quit()`.
5. `self._window.quit()` calls `self._root.destroy()` → Tk mainloop exits → control returns to `orchestrator.run()` → returns to `main.py:83`.
6. `main.py`'s `finally: guard.release()` at line 87 removes the lockfile.

**Critical ordering invariant.** Recording MUST stop before the process exits (otherwise orphan WAV in `%TEMP%`). Already correct today; the readiness gate does not affect shutdown.

**Rule 2 impact.** `_on_quit` runs on T1 by construction. State transitions inside stop() calls fire on T1. No new hazard.

### 4.5 Readiness gate (runs on T0-turning-T1) — no new cross-thread edge

`is_ready(config)` is pure. It runs on T0/T1 before `mainloop()` is entered — no background thread, no `dispatch`, no Rule 2 risk. Filesystem probes (Path.exists, is_dir, NamedTemporaryFile) are synchronous and bounded.

### 4.6 Summary: new boundaries = **zero**

This feature introduces zero new cross-thread boundaries. Every toast emission rides on an existing T→T1 dispatch path that is already tested. SC10 pins the withdrawn-mainloop-still-dispatches invariant. SC8 pins the self-exclusion chain.

---

## 5. Verification plan

Each SC traced to a pytest ID OR a manual-smoke step; SC coverage is exhaustive.

### 5.1 Unit tests (pytest — run on Windows and non-Windows where possible)

#### `tests/test_readiness.py` — **NEW** — covers SC2

```python
# 5 parametrized cases + constants check
```

- `test_ready_happy_path` — valid config (existing writable `transcript_dir`, non-empty `whisper_model`) → `(True, "")`.
- `test_transcript_dir_unset` — `transcript_dir=None` → `(False, REASON_TRANSCRIPT_DIR_UNSET)`.
- `test_transcript_dir_empty_string` — `transcript_dir=Path("")` → `(False, REASON_TRANSCRIPT_DIR_UNSET)` (empty string coerced).
- `test_transcript_dir_missing` — non-existent `tmp_path / "nope"` → `(False, REASON_TRANSCRIPT_DIR_MISSING.format(path=...))`.
- `test_transcript_dir_not_writable` — monkeypatch `tempfile.NamedTemporaryFile` to raise `PermissionError` → `(False, REASON_TRANSCRIPT_DIR_NOT_WRITABLE.format(...))`.
- `test_whisper_model_empty` — valid transcript_dir but `whisper_model=""` → `(False, REASON_WHISPER_MODEL_EMPTY)`.
- `test_constants_are_module_level` — asserts all four constants are importable and strings (guards against message drift).

Cross-platform: runs on Linux/macOS/CI (no Windows-only imports). No skipif.

#### `tests/test_config.py` — **MODIFY** — covers SC6 (config surface)

Add three cases to `TestConfigRoundTrip`:
- `test_notifications_defaults_when_missing` — write a TOML file without `[notifications]`, `load()` → all three bools `True`.
- `test_notifications_all_false_round_trip` — construct `Config(notify_started=False, notify_saved=False, notify_error=False)`, `save()`, `load()`, assert all `False`.
- `test_notifications_mixed_round_trip` — `notify_started=False`, `notify_saved=True`, `notify_error=True` through save/load.
- `test_notifications_section_header_emitted` — after `save()`, the rendered file contains the line `[notifications]` (pins ADR-8's nested-table choice).

Add one case to `TestConfigValidation`:
- `test_notifications_reject_non_bool` — `Config(notify_started="yes")` raises `ConfigError`.

Cross-platform.

#### `tests/test_orchestrator_tray_first.py` — **NEW** (Windows-only, skipif otherwise) — covers SC1, SC2, SC3, SC6

Mocks `AppWindow`, `TrayService`, `MicWatcher`, `TranscriptionService`, `RecordingService` via `unittest.mock`. Asserts:

- `test_ready_config_stays_hidden` — monkeypatch `readiness.is_ready` to return `(True, "")`, call the new readiness-gate code, assert `window.show` is NOT called and `window.switch_tab` is NOT called. (SC1)
- `test_unready_config_opens_settings` — monkeypatch `is_ready` to return `(False, "Transcript directory not set")`, assert `window.show` is called exactly once AND `window.switch_tab("Settings")` is called exactly once. (SC2)
- `test_notify_started_toggle_off_suppresses_toast` — construct orchestrator with `Config(notify_started=False, ...)`, call `_notify_if_enabled("started", title, body)`, assert `TrayService.notify` is NOT called, and assert `log.info` WAS called with the `[ORCH] notify.started: ...` line. (SC6)
- `test_notify_started_toggle_on_fires_toast` — same setup but `notify_started=True`, assert `TrayService.notify` called exactly once with `(title, body, on_click=...)`.
- `test_notify_saved_basename_only` — assert `_notify_if_enabled("saved", _TOAST_TITLE, _TOAST_BODY_SAVED.format(name="test.md"))` sends body `"Saved -> test.md"` with NO path separators. (SC4)
- `test_notify_body_truncated_to_60_chars` — long error string → body passed to tray is exactly 60 chars.

#### `tests/test_app_window_hidden_mainloop.py` — **NEW** (Windows-only, skipif otherwise) — covers SC10

SC10 pytest mechanics — **DESIGN DECISION** per DEFINE open question: use a real CTk root with `root.update()` in a spin loop. NOT `pytest-xvfb` (non-Windows-native, the app is Windows-only anyway). NOT mocking `after()` (would defeat the point of the test — we need to prove real `after()` works on a withdrawn root).

```python
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="CTk/Tk needed")

def test_dispatch_works_on_withdrawn_root() -> None:
    """SC10: AppWindow.dispatch delivers callbacks even when root was
    withdrawn at construction and never deiconified."""
    import threading
    from ui.app_window import AppWindow
    # minimal mocks for config / history_index / callbacks
    ...
    window = AppWindow(config=mock_cfg, history_index=mock_hi, on_save_config=lambda *_: None)
    # Verify the root is NOT viewable
    assert window._root.winfo_viewable() == 0

    fired = threading.Event()

    def target():
        window.dispatch(lambda: fired.set())

    threading.Thread(target=target, daemon=True).start()

    # Spin the mainloop briefly without entering it
    deadline = time.time() + 1.0
    while not fired.is_set() and time.time() < deadline:
        window._root.update()
        time.sleep(0.01)

    assert fired.is_set(), "dispatch did not fire on withdrawn root within 1s"
    window._root.destroy()
```

### 5.2 Regression guards (existing files — must still pass)

- `tests/test_self_exclusion_frozen.py` — verbatim pass (SC8). DESIGN changes nothing here.
- `tests/test_mic_watcher.py` — aliasing tests (`python.exe ↔ pythonw.exe`) still pass (SC8).
- Existing `tests/test_orchestrator*.py`, `tests/test_tray*.py` — any test that asserts `window.show` is called during `Orchestrator.run()` MUST be updated to inject a failing readiness predicate or accept the new gated-show semantics. Build agent audits each test and either (a) passes `Config(transcript_dir=None, ...)` to force the window open, or (b) asserts `show` is NOT called for ready configs. Specific files to audit (from a grep of `window.show`): see build agent's first step.

### 5.3 Manual Windows smoke tests (REQUIRED per memory `feedback_smoke_test_before_done`)

| SC | Manual step | Pass criterion |
|----|----|----|
| SC1 | `python src/main.py` on a valid config | Within 3s: tray icon present, NO window at any point, log shows `AppState.IDLE` → `ARMED` |
| SC2-a | Set `transcript_dir=""` in config.toml, launch | Window opens on Settings tab within 3s; log shows `"Readiness failed: Transcript directory not set"` |
| SC2-b | Delete `transcript_dir` from config.toml, launch | Same as SC2-a |
| SC2-c | Set `transcript_dir="C:\\Users\\nope"` (nonexistent), launch | Window opens on Settings tab; log shows `"...Transcript directory does not exist: ..."` |
| SC2-d | Set `whisper_model=""`, launch | Window opens on Settings tab; log shows `"...Whisper model is empty"` |
| SC3 | With valid config, open Teams/Zoom | Toast appears within one poll cycle reading `"Recording started — open to view captions"` |
| SC4 | Speak ~15s, close call | Toast appears reading `"Saved -> YYYY-MM-DD_HH-MM-SS_transcript.md"` (basename only, no full path) |
| SC5-a | Stop `LemonadeServer.exe`, trigger a recording | Toast appears reading `"Service error: ..."` (truncated to 60 chars); no full stack trace |
| SC5-b | On the BT-A2DP dev box (per memory `project_bt_a2dp_zero_capture`), trigger 4 consecutive silent recordings | Fourth attempt fires toast `"Capture issue — check audio settings"`; banner visible; log shows `"silent recordings in a row — pausing auto-rearm"` |
| SC6 | In Settings, flip `notify_started` to OFF, click Save, trigger a recording | No toast; INFO log line `[ORCH] notify.started: Recording started — open to view captions` STILL present |
| SC7 | Open window via tray-click, click X | Window hides, tray icon persists; trigger a recording → auto-flow works, no window re-show; click tray Quit → process exits, lockfile removed |
| SC9 | Build installer via `installer.iss`, install, sign out, sign in | Task Manager shows `MeetingRecorder.exe` running; tray icon visible; no window |

**Acceptance gate.** All 9 manual steps must pass on the user's daily-driver dev box before merging. Per memory `feedback_smoke_test_before_done`, unit tests alone do NOT close this task.

---

## 6. Quality gate self-check

| Check | Status |
|---|---|
| File manifest has no circular dependencies | PASS — strict order: readiness.py → config.py → (verify-only infra) → orchestrator.py → settings_tab.py → tests |
| Every ADR includes rejected alternatives | PASS — 8 ADRs, all have at least 2 rejected options |
| Every cross-thread boundary called out | PASS — §4 enumerates 4 T→T1 paths + shutdown ordering + readiness-gate synchronous path; zero new boundaries |
| Every SC from DEFINE traced to file + test | PASS — SC1-SC10 each map to at least one file change + at least one test or smoke step (see §5 table + §2 manifest column) |
| Windows-only constraints called out | PASS — ADR-3 notes single-path behavior; §5.1 marks Windows-only tests with skipif; §5.3 lists manual Windows steps |
| Critical Rules preserved | PASS — Rule 2 (Tk on T1) preserved via `_notify_if_enabled` choke point; Rule 4 (self-exclusion) explicit verify step + SC8 regression; Rule 5 (no vault paths) explicit in ADR-5; Rules 1/3/6/7/8 unaffected |
| No new runtime dependencies | PASS — `pystray`, `tomli_w`, `customtkinter` all already pinned |
| Readiness predicate reason strings are testable | PASS — four module-level constants in `readiness.py` pinned by `test_constants_are_module_level` |
| Memory notes honoured | PASS — `feedback_smoke_test_before_done` (mandatory §5.3), `reference_python_self_exclusion_aliasing` (SC8), `project_bt_a2dp_zero_capture` (SC5-b) |
| Installer unchanged | PASS — `installer.iss` verify-only; `{userstartup}` already delivers tray-only |

---

_Drafted 2026-04-20 for SDD Phase 2. Input: DEFINE_TRAY_FIRST_APP.md + BRAINSTORM_TRAY_FIRST_APP.md. Ready for `/build .claude/sdd/features/DESIGN_TRAY_FIRST_APP.md`._
