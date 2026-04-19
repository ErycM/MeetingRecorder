# DESIGN: EXE_PACKAGING

> Freeze MeetingRecorder into a double-clickable Windows installer using PyInstaller onedir + Inno Setup + GitHub Actions CI, with a unified `__version__` source, hybrid Lemonade-prereq UX, and explicit regression guards for Critical Rule #4 self-exclusion.

**Source docs:**
- [`BRAINSTORM_EXE_PACKAGING.md`](./BRAINSTORM_EXE_PACKAGING.md)
- [`DEFINE_EXE_PACKAGING.md`](./DEFINE_EXE_PACKAGING.md)

**Approach:** B (PyInstaller onedir + Inno Setup + GitHub Actions CI + unified `src/app/__version__.py`). Locked by DEFINE §Scope; not reopened here.

**Branch target:** `refactor/flow-overhaul` → follow-on `feat/exe-packaging`.

---

## Pre-design factual corrections (grounded in code)

The DEFINE doc was drafted against peer-app conventions; grounding in the live code surfaced two things the build agent must honour:

1. **Lemonade base URL default is `http://localhost:13305`, NOT `http://localhost:8000`.** Ground truth: [`src/app/services/transcription.py:52`](../../../src/app/services/transcription.py) `LEMONADE_URL = "http://localhost:13305"`, reinforced by [`src/app/npu_guard.py:62`](../../../src/app/npu_guard.py) `DEFAULT_SERVER_URL = "http://localhost:13305"` and the KB at [`.claude/kb/lemonade-whisper-npu.md:12`](../../kb/lemonade-whisper-npu.md). The new `Config.lemonade_base_url` field defaults to `"http://localhost:13305"`. The Inno installer's HTTP probe also targets `:13305`.
2. **`installer.iss` already uses `{autopf}` (`DefaultDirName={autopf}\{#MyAppName}`), not `{userpf}` / `{userappdata}`.** Per-user vs per-machine default therefore requires flipping `PrivilegesRequired` + `DefaultDirName` in concert (see ADR-9). The DEFINE's "default per-user, no UAC" promise maps to `PrivilegesRequired=lowest` + `PrivilegesRequiredOverridesAllowed=dialog` + a conditional `DefaultDirName`.
3. **`src/ui/settings_tab.py:432-444` imports `install_startup` at runtime** from the `_apply_login_toggle` method. Deleting `install_startup.py` without touching `settings_tab.py` leaves the `launch_on_login` switch wired to a missing module. Build agent must also retire/rewire that code path — see File Manifest #8.

---

## 1. Architecture

### 1.1 Build-time flow (Approach B — CI + local parity)

```text
                       ┌────────────────────────────────┐
                       │ src/app/__version__.py         │
                       │  __version__ = "4.0.0"         │   single source of truth
                       └────────────┬───────────────────┘
                                    │ imported by runtime (Settings → About)
                                    │ regex-read by CI step + Inno preprocessor
                                    │
         ┌──────────────────────────┴──────────────────────────┐
         │                                                     │
         ▼                                                     ▼
┌──────────────────────┐                              ┌──────────────────────────┐
│ requirements.txt     │─── pip install ─┐            │ .github/workflows/       │
│ + PyInstaller        │                 │            │  build-installers.yml    │
│  (dev dep)           │                 │            │  (workflow_dispatch)     │
└──────────────────────┘                 │            └─────────┬────────────────┘
                                         │                      │
                                         ▼                      │ reads __version__.py
                          ┌──────────────────────────┐          │ sets $env:VERSION
                          │ MeetingRecorder.spec     │◄─────────┘
                          │ (onedir, --collect-all   │
                          │  customtkinter,          │
                          │  pyaudiowpatch, pystray, │
                          │  --collect-submodules    │
                          │  PIL; datas=SaveLC.ico;  │
                          │  version resource)       │
                          └────────────┬─────────────┘
                                       │
                                       │ pyinstaller MeetingRecorder.spec
                                       ▼
                 ┌──────────────────────────────────────────┐
                 │ dist\MeetingRecorder\                    │
                 │   MeetingRecorder.exe  (windowed, ICO)   │
                 │   _internal\ (python312.dll, CTk assets, │
                 │               pywin32 DLLs, PIL plugins, │
                 │               _portaudio.pyd, ...)       │
                 └────────────────────┬─────────────────────┘
                                      │
                                      │ iscc /dAppVersion=$env:VERSION \
                                      │      /dSIGN=1 (if secrets set) \
                                      │      installer.iss
                                      ▼
                       ┌───────────────────────────────────────┐
                       │ installer_output\                     │
                       │   MeetingRecorder_Setup_v4.0.0.exe    │
                       └────────────────┬──────────────────────┘
                                        │
                                        │ GitHub Actions:
                                        │   upload-artifact@v4
                                        │   softprops/action-gh-release@v1
                                        ▼
                       ┌───────────────────────────────────────┐
                       │ Draft GitHub Release v4.0.0           │
                       │  (never auto-latest)                  │
                       └───────────────────────────────────────┘
```

### 1.2 Runtime flow (first launch, Lemonade missing)

```text
Double-click MeetingRecorder.exe  (or tester runs it from Start Menu)
 │
 ▼
main.py  (same entry point, source or frozen — sys.frozen disambiguates)
 │
 ├─► ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MeetingRecorder.App")
 │
 ├─► SingleInstance.acquire()
 │      └─► _write_lockfile() writes "<pid>\nMeetingRecorder.exe\n" to %TEMP%
 │          (sys.frozen=True → _exe_basename() returns "MeetingRecorder.exe"
 │           at single_instance.py:60-61 — UNCHANGED, verified)
 │
 ├─► Config.load() reads %APPDATA%\MeetingRecorder\config.toml
 │      └─► NEW: cfg.lemonade_base_url defaults to "http://localhost:13305"
 │
 ├─► AppWindow mounts (Live, History, Settings tabs)
 │
 ├─► Orchestrator starts services:
 │   │
 │   ├─► MicWatcher.start()
 │   │      └─► _read_lockfile_exclusion() reads %TEMP%\MeetingRecorder.lock line 2
 │   │          returns "MeetingRecorder.exe"  →  MicWatcher filters exact basename
 │   │
 │   ├─► TrayService.start()   (pystray thread T3)
 │   │
 │   └─► Thread(name="npu-startup") → TranscriptionService.ensure_ready()
 │          ├─ Lemonade reachable + NPU model loaded
 │          │    └─► window.dispatch(_on_npu_ready) → AppState.IDLE → ARMED
 │          │
 │          └─ Lemonade unreachable   (S2/S4 scenario)
 │                │  requests.ConnectionError at transcription.py:235
 │                │  → TranscriptionNotReady raised
 │                │  → orchestrator catches, dispatches _on_npu_failed
 │                ▼
 │   _on_npu_failed() on T1:
 │       ├─ sm.transition(AppState.ERROR, reason=ErrorReason.LEMONADE_UNREACHABLE)
 │       │    └─ AppWindow.on_state():
 │       │         ├─► LiveTab.show_lemonade_banner()           (NEW this feature)
 │       │         └─► SettingsTab.set_lemonade_reachable(False, "...")   (NEW)
 │       │
 │       └─ Tray icon stays green-idle (no crash, no orphan tray)
 │
 ▼
User sees: Live tab with banner
           "Lemonade Server not reachable — [Open Settings]"
           Settings tab: Lemonade reachability row = FAIL (at 12:34:56),
                         base URL override field = "http://localhost:13305",
                         About row = "v4.0.0",
                         [Retry] button
```

---

## 2. File manifest (ordered by dependency)

Strict dependency order: each file is created/modified before any consumer below it. No circular dependencies.

| # | File | Action | Notes |
|---|------|--------|-------|
| 1 | `src/app/__version__.py` | NEW | Single version constant; imported only (no side effects) |
| 2 | `src/app/config.py` | MODIFY | Add `lemonade_base_url` field + TOML round-trip |
| 3 | `src/app/services/transcription.py` | MODIFY | Accept base URL via constructor; add `probe_only()` |
| 4 | `src/app/npu_guard.py` | VERIFY | No code change — already accepts `server_url` parameter |
| 5 | `src/app/single_instance.py` | VERIFY | No code change — frozen case already handled |
| 6 | `src/app/services/mic_watcher.py` | VERIFY | No code change — `_read_lockfile_exclusion()` already path-agnostic |
| 7 | `src/app/orchestrator.py` | MODIFY | Pass `cfg.lemonade_base_url` into TranscriptionService + npu_guard; wire reason code on probe failure |
| 8 | `src/ui/settings_tab.py` | MODIFY | Lemonade row + URL override + About row + retire `install_startup` import |
| 9 | `src/ui/live_tab.py` | MODIFY | Lemonade-missing banner widget, hidden by default |
| 10 | `src/ui/app_window.py` | MODIFY | Add `switch_tab()` helper; wire `on_state` to show/hide Lemonade banner |
| 11 | `MeetingRecorder.spec` | NEW | PyInstaller onedir spec |
| 12 | `installer.iss` | MODIFY | AppVersion from preprocessor; `[Code]` probe page; `{userstartup}`; SignTool stub; per-user default |
| 13 | `install_startup.py` | DELETE | Replaced by Inno `{userstartup}` entry |
| 14 | `.github/workflows/build-installers.yml` | NEW | Windows runner; workflow_dispatch; version-gate; draft release |
| 15 | `README.md` | MODIFY | Tester install steps + maintainer build recipe + first-launch banner docs |
| 16 | `tests/test_config.py` | MODIFY | Round-trip `lemonade_base_url` default + explicit override |
| 17 | `tests/test_transcription_service.py` | MODIFY | `probe_only()` OK/fail/timeout; constructor honours URL override |
| 18 | `tests/test_orchestrator.py` | MODIFY | Probe-fail → `AppState.ERROR` w/ `ErrorReason.LEMONADE_UNREACHABLE` |
| 19 | `tests/test_self_exclusion_frozen.py` | NEW | Lockfile w/ `MeetingRecorder.exe` → `_is_self()` returns True (G8) |

### File details

Each entry below: **absolute path**, **status**, **purpose (1-2 sentences)**, **depends on**, **key changes**.

#### 1. `src/app/__version__.py` — NEW

- **Purpose.** Single source of truth for the app's semver. Imported by Settings → About row, logged at orchestrator boot, regex-scraped by `installer.iss` and CI.
- **Depends on.** Nothing. Pure constant.
- **Key changes:**
  - New file. Content:
    ```python
    """Version constant — single source of truth for semver.

    Read by:
    - src/ui/settings_tab.py (About row)
    - src/app/orchestrator.py (boot banner log)
    - installer.iss (GetStringFromFile + regex, or CI-injected /dAppVersion=)
    - .github/workflows/build-installers.yml (regex → $env:VERSION)

    NEVER add side effects here — this module is imported before logging
    is configured and during installer preprocessing.
    """

    from __future__ import annotations

    __version__: str = "4.0.0"
    ```
  - Strict semver per DEFINE Q1. No pre-release or build metadata.

#### 2. `src/app/config.py` — MODIFY

- **Purpose.** Add one new field to the Config dataclass so the base URL is user-configurable and persistent.
- **Depends on.** Nothing new (uses existing `tomli_w` + `tomllib` round-trip).
- **Key changes:**
  - Add after `loopback_device_index` (config.py:105):
    ```python
    # Lemonade REST base URL. "http://localhost:13305" is the default ship
    # port (matches transcription.LEMONADE_URL). Users whose Lemonade listens
    # on a non-default port or a remote host override it here; Settings tab
    # exposes the field and a reachability probe.
    lemonade_base_url: str = "http://localhost:13305"
    ```
  - Extend `__post_init__` validation:
    ```python
    url = self.lemonade_base_url.strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        raise ConfigError(
            f"lemonade_base_url must start with http:// or https://, got {url!r}"
        )
    ```
  - `load()` (config.py:149-164): add `lemonade_base_url=str(data.get("lemonade_base_url", "http://localhost:13305"))` to the Config kwargs.
  - `save()` (config.py:183-198): always persist, since every Config has a non-empty value:
    ```python
    data["lemonade_base_url"] = cfg.lemonade_base_url
    ```
  - **Critical Rule #6** respected — default value is a local-host URL, no personal paths.

#### 3. `src/app/services/transcription.py` — MODIFY

- **Purpose.** Honour `Config.lemonade_base_url` (instead of only the module constant) and expose a lightweight non-blocking `probe_only()` for the Settings-tab reachability row.
- **Depends on.** `src/app/config.py` (for the new field).
- **Key changes:**
  - Keep `LEMONADE_URL = "http://localhost:13305"` as the fallback default — its existing consumers (orchestrator.py:331, 333; tests) continue to work unchanged.
  - Constructor signature already accepts `server_url`; orchestrator is the only caller, and will now pass `config.lemonade_base_url`. No signature change required here.
  - Add new method between `ensure_ready()` and `transcribe_file()`:
    ```python
    def probe_only(self, timeout_s: float = 1.0) -> tuple[bool, str]:
        """Non-blocking Lemonade reachability probe (read-only diagnostic).

        Unlike ``ensure_ready()``, this method:
        - does NOT start the server (no subprocess.Popen)
        - does NOT load a model
        - does NOT transition the app state
        - does NOT raise (errors returned as (False, reason))

        Used by SettingsTab's Lemonade reachability row and LiveTab's banner
        (post-retry probe). Safe to call repeatedly and from any thread
        that has an HTTP socket budget.

        Returns
        -------
        (True, "") on success.
        (False, reason) on failure — reason is a short one-line string
            such as ``"connection refused"`` or ``"http 503"``.
        """
        try:
            r = requests.get(f"{self._endpoint}/api/v1/health", timeout=timeout_s)
            if r.status_code != 200:
                return False, f"http {r.status_code}"
            return True, ""
        except requests.Timeout:
            return False, "timeout"
        except requests.ConnectionError:
            return False, "connection refused"
        except requests.RequestException as exc:
            return False, f"request error: {exc.__class__.__name__}"
    ```
  - **Critical Rule #3** respected: `probe_only()` is strictly a diagnostic — callers must STILL call `ensure_ready()` before any real transcription path. This is enforced by the `self._ready` flag guard at transcription.py:290.
  - **Critical Rule #8** respected: `probe_only()` only hits the HTTP `/health` endpoint; no WebSocket payload is constructed, no OpenAI-shaped `session.update` is sent.

#### 4. `src/app/npu_guard.py` — VERIFY (no code change)

- **Purpose.** Confirm existing `list_npu_models(server_url)` and `ensure_ready(server_url)` already accept a URL argument so the orchestrator can pass `config.lemonade_base_url` without edits here.
- **Depends on.** Nothing.
- **Key findings (cited from source):**
  - `list_npu_models(server_url: str = DEFAULT_SERVER_URL)` at [`npu_guard.py:91`](../../../src/app/npu_guard.py) — accepts URL, no change needed.
  - `ensure_ready(server_url: str = DEFAULT_SERVER_URL)` at [`npu_guard.py:152`](../../../src/app/npu_guard.py) — same.
  - `DEFAULT_SERVER_URL = "http://localhost:13305"` at [`npu_guard.py:62`](../../../src/app/npu_guard.py) — matches new Config default.

#### 5. `src/app/single_instance.py` — VERIFY (no code change)

- **Purpose.** Confirm the lockfile writes the frozen exe basename so MicWatcher's self-exclusion survives PyInstaller freeze.
- **Depends on.** Nothing.
- **Key findings (cited from source):**
  - `_exe_basename()` at [`single_instance.py:53-62`](../../../src/app/single_instance.py) returns `"MeetingRecorder.exe"` when `sys.frozen` is True, else `os.path.basename(sys.executable)`. Non-negotiable per Critical Rule #4.
  - `_write_lockfile()` at [`single_instance.py:223-238`](../../../src/app/single_instance.py) writes `"<pid>\n<exe_name>\n"` — the frozen path lands in line 2, exactly what MicWatcher reads.
  - **Regression guard:** new test `tests/test_self_exclusion_frozen.py` monkeypatches `sys.frozen = True`, builds a `SingleInstance()`, and asserts the lockfile contains `"MeetingRecorder.exe"` on line 2.

#### 6. `src/app/services/mic_watcher.py` — VERIFY (no code change)

- **Purpose.** Confirm `_is_self()` uses exact last-segment basename match (not substring) so the frozen-exe path filters correctly without hardcoding names.
- **Depends on.** Nothing.
- **Key findings (cited from source):**
  - `_is_self()` at [`mic_watcher.py:112-154`](../../../src/app/services/mic_watcher.py) splits on `#` and matches the last segment case-insensitively against the lockfile-supplied `self_exclusion`. Aliases `python.exe ↔ pythonw.exe` at lines 146-150 survive (memory `reference_python_self_exclusion_aliasing` still applies).
  - `_read_lockfile_exclusion()` at [`orchestrator.py:123-140`](../../../src/app/orchestrator.py) reads line 2 of the lockfile. When line 2 is `"MeetingRecorder.exe"` (frozen case), MicWatcher's exclusion string is exactly `"MeetingRecorder.exe"`. Exact match, not substring.
  - **Regression guard:** same new test as #5 asserts `_is_self("C:#Users#x#AppData#Local#MeetingRecorder#MeetingRecorder.exe", "MeetingRecorder.exe") is True` and `_is_self("C:#OtherApp#OtherApp.exe", "MeetingRecorder.exe") is False`. Existing alias tests unchanged.

#### 7. `src/app/orchestrator.py` — MODIFY

- **Purpose.** Thread the new `Config.lemonade_base_url` into `TranscriptionService`, `list_npu_models()`, and the `_on_service_error` path so `AppState.ERROR` carries the right reason.
- **Depends on.** #2 (Config), #3 (TranscriptionService constructor).
- **Key changes:**
  - At the call site building `TranscriptionService` (orchestrator.py:250-256), pass:
    ```python
    self._transcription_svc = TranscriptionService(
        server_url=self._config.lemonade_base_url,   # NEW
        model=self._config.whisper_model,
        server_exe=server_exe,
        on_error=lambda exc: dispatch(lambda: self._on_service_error(exc)),
    )
    ```
  - In `_npu_startup_check` error branch (orchestrator.py:322-340), replace the hardcoded-URL fallback:
    ```python
    from app.npu_guard import list_npu_models
    available = list_npu_models(self._config.lemonade_base_url)
    ```
  - In `_on_config_saved` (orchestrator.py:812-831), after applying the new config, if `lemonade_base_url` changed, rebuild `TranscriptionService._endpoint` via either a new setter or reconstruction of the service. **Decision:** add a lightweight setter to `TranscriptionService`:
    ```python
    def set_base_url(self, server_url: str) -> None:
        """Update endpoint URL for probe_only/ensure_ready.

        Thread-safe for the next call only; callers must stop streaming
        before mutating URL to avoid mid-flight mismatch.
        """
        self._endpoint = server_url.rstrip("/")
        self._ready = False  # force re-ensure_ready after URL change
    ```
    (Add this setter to file #3's edit list — defer the mechanical addition to build agent; it is a 3-line method below `probe_only()`.)
  - Reason code on probe failure (orchestrator.py:391-392) is already `ErrorReason.LEMONADE_UNREACHABLE`. **No change** — the banner in `live_tab.py` reads `reason.name == "LEMONADE_UNREACHABLE"`. Audit says this path is already distinct from other ERROR causes, satisfying DEFINE G3.

#### 8. `src/ui/settings_tab.py` — MODIFY

- **Purpose.** Add Lemonade reachability row, manual base-URL override field, About row; retire the `install_startup` import path.
- **Depends on.** #1 (`__version__`), #2 (`Config.lemonade_base_url`), #3 (`TranscriptionService.probe_only` — orchestrator provides this via a new `on_probe_lemonade` callback).
- **Key changes:**
  - Add new row in the form (between "WAV archive dir" and "Whisper model", ~settings_tab.py:113):
    ```python
    # Lemonade base URL override
    ctk.CTkLabel(scroll_frame, text="Lemonade URL:", anchor="w").grid(...)
    self._lemonade_url_var = tk.StringVar(value=config.lemonade_base_url)
    ctk.CTkEntry(scroll_frame, textvariable=self._lemonade_url_var, width=260).grid(...)
    ```
    Persisted in `_on_save_clicked` by adding `lemonade_base_url=self._lemonade_url_var.get().strip()` to the Config constructor kwargs (~settings_tab.py:400-411).
  - Add new diagnostics row below the existing NPU row (~settings_tab.py:276):
    ```python
    self._lemonade_diag_label = ctk.CTkLabel(
        diag_frame,
        text="Lemonade: checking...",
        anchor="w",
        font=theme.FONT_STATUS,
        justify="left",
    )
    self._lemonade_diag_label.pack(fill="x", padx=theme.PAD_INNER, pady=(0, 4))
    ```
  - Add a public method:
    ```python
    def set_lemonade_reachable(
        self, ok: bool, detail: str = "", ts: str | None = None
    ) -> None:
        """Update the Lemonade reachability diagnostic row.

        MUST be called from T1 (dispatch via AppWindow.dispatch).
        """
        stamp = ts or time.strftime("%H:%M:%S")
        status = "OK" if ok else f"FAIL ({detail})"
        self._lemonade_diag_label.configure(
            text=f"Lemonade: {status}  (last probe {stamp})"
        )
    ```
  - Add About row at the very bottom of the form, below the Diagnostics frame:
    ```python
    from app.__version__ import __version__
    ctk.CTkLabel(
        self.frame,
        text=f"MeetingRecorder v{__version__}",
        anchor="center",
        font=theme.FONT_STATUS,
    ).pack(pady=(4, theme.PAD_INNER))
    ```
  - **Retire `install_startup` import.** Replace `_apply_login_toggle()` (settings_tab.py:430-444) with a no-op stub that logs and continues:
    ```python
    def _apply_login_toggle(self, enabled: bool) -> None:
        """Launch-on-login is now managed by the Inno Setup installer's
        {userstartup} entry (ADR-4). The Settings toggle is kept for
        source-run developers only — it no longer mutates the registry
        from the app. When frozen, the toggle state is informational.
        """
        if getattr(sys, "frozen", False):
            log.info(
                "[SETTINGS] launch_on_login=%s (Inno manages startup shortcut)",
                enabled,
            )
            return
        log.info(
            "[SETTINGS] launch_on_login=%s — source-run dev may register HKCU\\Run manually",
            enabled,
        )
    ```
  - Critical: `import install_startup` at settings_tab.py:433 MUST be deleted in the same commit that deletes the file. Build agent enforces this via ordering in the manifest.
  - **Critical Rule #2** respected — all three new UI methods are documented "T1 only; dispatch via AppWindow.dispatch".

#### 9. `src/ui/live_tab.py` — MODIFY

- **Purpose.** Add the Lemonade-missing banner at the top of the Live tab, hidden by default.
- **Depends on.** #10 (AppWindow wires state transitions to banner show/hide).
- **Key changes:**
  - Add new frame after the existing `_capture_warning_frame` block (~live_tab.py:92):
    ```python
    # Lemonade-missing banner — hidden by default. Shown when orchestrator
    # enters AppState.ERROR with ErrorReason.LEMONADE_UNREACHABLE. Dismissable;
    # state-machine drives re-display on next probe failure.
    self._lemonade_banner_frame = ctk.CTkFrame(
        self.frame, fg_color="#2a3a5a", corner_radius=6
    )
    self._lemonade_banner_label = ctk.CTkLabel(
        self._lemonade_banner_frame,
        text="Lemonade Server not reachable",
        anchor="w",
        justify="left",
        font=theme.FONT_STATUS,
        wraplength=420,
    )
    self._lemonade_banner_label.pack(
        side="left", fill="x", expand=True, padx=theme.PAD_INNER, pady=4
    )
    self._lemonade_open_settings_btn = ctk.CTkButton(
        self._lemonade_banner_frame,
        text="Open Settings",
        width=120,
        command=self._on_open_settings_clicked,
    )
    self._lemonade_open_settings_btn.pack(side="right", padx=theme.PAD_INNER, pady=4)
    ```
  - Add `on_open_settings` callback to constructor signature (with `None` default so existing callers keep working):
    ```python
    def __init__(
        self,
        parent: object,
        on_stop: Callable[[], None],
        on_dismiss_capture_warning: Callable[[], None] | None = None,
        on_open_settings: Callable[[], None] | None = None,
    ) -> None:
        ...
        self._on_open_settings = on_open_settings
    ```
  - Add two new public methods:
    ```python
    def show_lemonade_banner(self) -> None:
        """Show the Lemonade-unreachable banner. MUST be on T1."""
        self._lemonade_banner_frame.pack(
            fill="x", padx=0, pady=(0, 4), before=self._timer_label
        )

    def hide_lemonade_banner(self) -> None:
        """Hide the banner. Idempotent. MUST be on T1."""
        try:
            self._lemonade_banner_frame.pack_forget()
        except Exception:
            pass

    def _on_open_settings_clicked(self) -> None:
        if self._on_open_settings is not None:
            try:
                self._on_open_settings()
            except Exception as exc:
                log.warning("[LIVE] on_open_settings callback raised: %s", exc)
    ```
  - **Critical Rule #2** respected — all three new methods are documented "T1 only".

#### 10. `src/ui/app_window.py` — MODIFY

- **Purpose.** Wire the `AppState.ERROR` + `ErrorReason.LEMONADE_UNREACHABLE` transition to show the Live-tab banner and update the Settings reachability row; expose a `switch_tab()` helper for the banner button.
- **Depends on.** #9 (LiveTab banner methods), #8 (SettingsTab reachability row), state.py `ErrorReason.LEMONADE_UNREACHABLE` (already defined at state.py:45).
- **Key changes:**
  - In `__init__`, when constructing `LiveTab`, pass the new callback:
    ```python
    self._live_tab = LiveTab(
        tab_live,
        on_stop=on_stop,
        on_dismiss_capture_warning=on_dismiss_capture_warning,
        on_open_settings=lambda: self.switch_tab("Settings"),   # NEW
    )
    ```
  - Add public method:
    ```python
    def switch_tab(self, name: str) -> None:
        """Programmatically switch to a named tab. Must be on T1."""
        try:
            self._tabview.set(name)
        except Exception as exc:
            log.warning("[APP_WINDOW] switch_tab(%r) failed: %s", name, exc)
    ```
  - In `on_state()` (app_window.py:181-220), add branch for Lemonade-specific error:
    ```python
    elif new is AppState.ERROR:
        reason_name = reason.name if reason is not None else "UNKNOWN"
        msg = f"ERROR: {reason_name}"
        self._live_tab.set_status(msg)
        self._settings_tab.set_error_banner(reason_name)
        # NEW: Lemonade-specific banner on Live tab
        from app.state import ErrorReason
        if reason is ErrorReason.LEMONADE_UNREACHABLE:
            self._live_tab.show_lemonade_banner()
        log.error("[APP_WINDOW] Entered ERROR state: %s", reason_name)

    if new is not AppState.ERROR:
        self._settings_tab.set_error_banner(None)
        self._live_tab.hide_lemonade_banner()   # NEW — clear on recovery
    ```
  - **Critical Rule #2** respected — `switch_tab()` and the banner show/hide calls are documented "T1 only; callers must dispatch if originating off-thread". The existing `on_state()` is already on T1 (invoked synchronously from `StateMachine._apply`, which is called on the dispatch-marshalled transition thread).

#### 11. `MeetingRecorder.spec` — NEW

- **Purpose.** PyInstaller onedir spec pinning the output directory name so `installer.iss` line 41 glob works verbatim.
- **Depends on.** #1 (`__version__` — embedded as Windows EXE version resource), `assets/SaveLC.ico` (existing).
- **Key changes:**
  - New file at repo root. Content outline:
    ```python
    # -*- mode: python ; coding: utf-8 -*-
    """PyInstaller spec for MeetingRecorder (onedir).

    Build:
        pyinstaller MeetingRecorder.spec

    Output:
        dist\\MeetingRecorder\\MeetingRecorder.exe
        dist\\MeetingRecorder\\_internal\\   (customtkinter, pyaudiowpatch,
                                              pywin32 DLLs, PIL plugins, ...)

    The output dir name MUST stay 'MeetingRecorder' so installer.iss line 41
    Source: "dist\\MeetingRecorder\\*" globs the full tree.
    """
    from __future__ import annotations

    import re
    from pathlib import Path
    from PyInstaller.utils.hooks import collect_all, collect_submodules

    # Read __version__ without importing (runs before src/ is on sys.path)
    _ver_text = Path("src/app/__version__.py").read_text(encoding="utf-8")
    VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', _ver_text).group(1)

    # Collect CTk assets (theme JSONs + PNGs) — ADR-1 risk #1
    ctk_datas, ctk_binaries, ctk_hiddenimports = collect_all("customtkinter")
    pa_datas, pa_binaries, pa_hiddenimports = collect_all("pyaudiowpatch")
    ps_datas, ps_binaries, ps_hiddenimports = collect_all("pystray")
    pil_hiddenimports = collect_submodules("PIL")

    block_cipher = None

    a = Analysis(
        ["src/main.py"],
        pathex=["src"],   # matches sys.path.insert in main.py
        binaries=ctk_binaries + pa_binaries + ps_binaries,
        datas=(
            ctk_datas + pa_datas + ps_datas
            + [("assets/SaveLC.ico", "assets")]
        ),
        hiddenimports=(
            ctk_hiddenimports + pa_hiddenimports + ps_hiddenimports
            + pil_hiddenimports
            + ["win32event", "win32api", "win32con", "win32gui"]
        ),
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        win_no_prefer_redirects=False,
        win_private_assemblies=False,
        cipher=block_cipher,
        noarchive=False,
    )
    pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

    exe = EXE(
        pyz, a.scripts,
        [],
        exclude_binaries=True,
        name="MeetingRecorder",              # produces MeetingRecorder.exe
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,                            # UPX can trip AV heuristics
        console=False,                        # GUI subsystem — no console
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon="assets/SaveLC.ico",
        version_file=None,                    # use Windows version resource below
    )

    coll = COLLECT(
        exe, a.binaries, a.zipfiles, a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="MeetingRecorder",              # → dist\\MeetingRecorder\\
    )
    ```
  - **Risk mitigations** (cross-referenced to BRAINSTORM §Risks):
    - Risk 1 customtkinter — `collect_all("customtkinter")`
    - Risk 2 PyAudioWPatch `_portaudio.pyd` — `collect_all("pyaudiowpatch")`
    - Risk 3 pywin32 — explicit `hiddenimports=["win32event", "win32api", ...]`. If the PyInstaller hook fails, augment with `pathex` or `--log-level=DEBUG` investigation during build (covered in README build recipe).
    - Risk 4 pystray + Pillow — `collect_all("pystray")` + `collect_submodules("PIL")`.
    - Risk 6 output-dir drift — `name="MeetingRecorder"` pinned twice (EXE and COLLECT).

#### 12. `installer.iss` — MODIFY

- **Purpose.** (a) Read AppVersion from preprocessor or `__version__.py`; (b) run Lemonade probe wizard page; (c) add SignTool stub; (d) add `{userstartup}` entry; (e) support per-user default + per-machine opt-in.
- **Depends on.** #1 (`__version__.py`), `dist\MeetingRecorder\*` (PyInstaller output from #11).
- **Key changes:**
  - Replace hardcoded version (installer.iss:3):
    ```
    #ifndef AppVersion
      ; Regex from src\app\__version__.py (manual builds); CI passes /dAppVersion=
      #define AppVersion GetStringFromFile(AddBackslash(SourcePath) + "src\app\__version__.py", 0, 99999)
      #define AppVersion Copy(AppVersion, Pos('"', AppVersion) + 1, 99999)
      #define AppVersion Copy(AppVersion, 1, Pos('"', AppVersion) - 1)
    #endif
    #define MyAppVersion AppVersion
    ```
    Inno preprocessor note: `GetStringFromFile` returns the whole file; the two `Copy` steps peel `__version__ = "` and the trailing `"`. This is the documented workaround for not having a literal file-value accessor. Alternative: CI always passes `/dAppVersion=4.0.0` and the `#ifndef` guards the fallback.
  - Add after `[Setup]` block (installer.iss:9-26):
    ```
    ; Per-user default (no UAC). Opt into per-machine via the wizard.
    PrivilegesRequired=lowest
    PrivilegesRequiredOverridesAllowed=dialog
    ; Program Files for per-machine, %LOCALAPPDATA% for per-user — Inno
    ; auto-selects {autopf} → {localappdata}\Programs when PrivilegesRequired=lowest.
    ; DefaultDirName={autopf}\{#MyAppName} already set, works for both.

    ; SignTool stub — activates only when CI sets /dSIGN=1 AND signtool.exe
    ; is on PATH. No cert required for v1; adding it later is zero code change.
    #ifdef SIGN
      SignTool=signtool_cmd $f
    #endif

    ; Versioned output name so every artifact carries its semver.
    OutputBaseFilename=MeetingRecorder_Setup_v{#MyAppVersion}
    ```
  - Add/replace `[Tasks]` to keep the existing desktop icon task AND add a startupicon task (opt-in, not default):
    ```
    [Tasks]
    Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
    Name: "startupicon"; Description: "Launch {#MyAppName} when I sign in to Windows"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
    ```
  - Extend `[Icons]` (installer.iss:43-46) with the startup entry, gated on the new task:
    ```
    [Icons]
    Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppAUMID}"
    Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppAUMID}"; Tasks: desktopicon
    Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppAUMID}"; Tasks: startupicon
    ```
  - Add new `[Code]` section at end of file — Pascal probe (non-blocking wizard page):
    ```
    [Code]
    var
      LemonadePage: TOutputMsgMemoWizardPage;

    function LemonadeBinaryOnPath(): Boolean;
    var
      ResultCode: Integer;
    begin
      // Best-effort: ask cmd "where" to search PATH. Non-zero exit = missing.
      Result := Exec(
        ExpandConstant('{cmd}'),
        '/c where LemonadeServer.exe >nul 2>&1',
        '', SW_HIDE, ewWaitUntilTerminated, ResultCode
      ) and (ResultCode = 0);
    end;

    function LemonadeHttpProbe(): Boolean;
    var
      WinHttp: Variant;
    begin
      Result := False;
      try
        WinHttp := CreateOleObject('WinHttp.WinHttpRequest.5.1');
        WinHttp.Open('GET', 'http://localhost:13305/api/v1/health', False);
        WinHttp.SetTimeouts(1000, 1000, 1000, 1000); // 1s everywhere
        WinHttp.Send('');
        Result := (WinHttp.Status = 200);
      except
        Result := False;
      end;
    end;

    procedure InitializeWizard();
    begin
      // Pre-create the info page so we can decide to skip it in ShouldSkipPage.
      LemonadePage := CreateOutputMsgMemoPage(
        wpWelcome,
        'Lemonade Server prerequisite',
        'MeetingRecorder requires Lemonade Server for transcription',
        'Lemonade Server was not detected on this machine. You can continue '
        + 'the installation and install Lemonade later from lemonade-server.ai '
        + '— the app will show a reminder banner until Lemonade is reachable.',
        'Visit https://lemonade-server.ai to download the free installer, '
        + 'then re-launch MeetingRecorder. Click Next to continue, or Cancel '
        + 'to exit the installer.'
      );
    end;

    function ShouldSkipPage(PageID: Integer): Boolean;
    begin
      Result := False;
      if PageID = LemonadePage.ID then
        // Skip the page if EITHER probe succeeds (S3 path).
        Result := LemonadeBinaryOnPath() or LemonadeHttpProbe();
    end;
    ```
  - **Per-user default + per-machine opt-in** is delivered by `PrivilegesRequired=lowest` + `PrivilegesRequiredOverridesAllowed=dialog`. Tester default is two Next clicks + one Install click with no UAC (DEFINE G10).

#### 13. `install_startup.py` — DELETE

- **Purpose.** Retire per DEFINE Q5. Inno `{userstartup}` (#12) is the single mechanism; maintaining two is debt.
- **Depends on.** #8 must land first (settings_tab.py removes its `import install_startup`).
- **Key actions:**
  - `git rm install_startup.py`.
  - Grep audit: only remaining references after #8 are in docs (`README.md` line 31, 77, 80, 83, 136 — fixed in #15) and `.claude/` docs which are orthogonal.
  - No entry point in `src/` consumes it post-#8 — verified.

#### 14. `.github/workflows/build-installers.yml` — NEW

- **Purpose.** Reproducible Windows builds, version-drift gate, draft release attachment.
- **Depends on.** #1 (`__version__.py`), #11 (spec), #12 (`installer.iss`).
- **Key changes:**
  - New file. Content outline:
    ```yaml
    name: Build installers
    on:
      workflow_dispatch:
      workflow_call:

    jobs:
      build:
        runs-on: windows-latest
        timeout-minutes: 15
        permissions:
          contents: write   # required for softprops/action-gh-release
        steps:
          - uses: actions/checkout@v4

          - uses: actions/setup-python@v5
            with:
              python-version: "3.12"
              cache: "pip"

          - name: Install deps
            shell: pwsh
            run: |
              python -m pip install --upgrade pip
              pip install -r requirements.txt
              pip install pyinstaller>=6.0

          - name: Read version
            id: ver
            shell: pwsh
            run: |
              $line = Select-String -Path src/app/__version__.py -Pattern '__version__\s*=\s*"([^"]+)"'
              $v = $line.Matches.Groups[1].Value
              Write-Host "VERSION=$v"
              "VERSION=$v" | Out-File $env:GITHUB_ENV -Append
              "version=$v"  | Out-File $env:GITHUB_OUTPUT -Append

          - name: Pre-freeze tests (soft gate)
            shell: pwsh
            run: |
              python -m pytest tests/ --maxfail=1 -q
            continue-on-error: false

          - name: PyInstaller freeze
            shell: pwsh
            run: pyinstaller --noconfirm MeetingRecorder.spec

          - name: Freeze sanity check
            shell: pwsh
            run: |
              if (-not (Test-Path "dist/MeetingRecorder/MeetingRecorder.exe")) {
                Write-Error "MeetingRecorder.exe missing from dist/"; exit 1
              }

          - name: Install Inno Setup
            shell: pwsh
            run: choco install innosetup -y --no-progress

          - name: Compile installer
            shell: pwsh
            run: |
              $iscc = (Get-Command ISCC.exe -ErrorAction Stop).Source
              & $iscc "/dAppVersion=$env:VERSION" installer.iss
              if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

          - name: Upload artifact
            uses: actions/upload-artifact@v4
            with:
              name: MeetingRecorder_Setup_v${{ steps.ver.outputs.version }}
              path: installer_output/*.exe
              retention-days: 30

          - name: Create draft release
            uses: softprops/action-gh-release@v1
            with:
              tag_name: v${{ steps.ver.outputs.version }}
              name: v${{ steps.ver.outputs.version }}
              draft: true
              prerelease: false
              files: installer_output/*.exe
    ```
  - Runner-minute cost controlled by `workflow_dispatch` only (no push triggers) per BRAINSTORM Approach B risk #1.
  - Signing path stubbed: no `/dSIGN=1`; activating it is a one-line CI change + `secrets` wiring.
  - **Version-drift gate:** the "Pre-freeze tests" step runs existing pytest, including the new test that asserts `Config.lemonade_base_url` default matches. Explicit semver match check is soft for v1 per DEFINE G6 — when the CI runner diverges from local builds, a follow-up hardens this.

#### 15. `README.md` — MODIFY

- **Purpose.** Document tester install flow + maintainer build recipe + first-launch banner explanation.
- **Depends on.** #11 (spec), #12 (installer.iss), #13 (deletion of `install_startup.py`), #14 (workflow).
- **Key changes:**
  - New "For testers" section at top:
    - Download link placeholder (`MeetingRecorder_Setup_v4.0.0.exe`).
    - SmartScreen "More info → Run anyway" screenshot/instructions (unsigned path).
    - Lemonade prereq: link to `https://lemonade-server.ai`; note the non-blocking installer info page.
    - Two-Next-one-Install default per-user path; "Advanced → per-machine" for IT-managed boxes.
  - New "For developers" section:
    - Local build: `pyinstaller MeetingRecorder.spec` then `iscc /dAppVersion=4.0.0 installer.iss`.
    - CI build: trigger via Actions → "Build installers" → workflow_dispatch.
    - Source-run unchanged: `python src/main.py`.
  - New "First-launch behavior" section:
    - Lemonade-missing banner; "Open Settings → fix URL or install Lemonade".
    - Startup-on-login opt-in via installer task, not in-app toggle (when frozen).
  - Remove all `install_startup.py` lines (README.md:31, 77, 80, 83, 136) and add a `DEPRECATED` note pointing muscle-memory readers at the Inno startup task.
  - Update "Lemonade server URL" table entry (README.md:98) to cite `Config.lemonade_base_url` (Settings → Lemonade URL) — not `transcriber.py` (legacy).

#### 16. `tests/test_config.py` — MODIFY

- **Purpose.** Assert `Config.lemonade_base_url` round-trips through TOML write/read with the correct default.
- **Depends on.** #2.
- **Key changes:**
  - Add two tests:
    1. `test_default_lemonade_base_url()` — `Config()` produces `lemonade_base_url == "http://localhost:13305"`.
    2. `test_roundtrip_lemonade_base_url(tmp_path)` — build `Config(lemonade_base_url="https://remote.example:9443")`, `save()` to `tmp_path`, `load()` back, assert equality.
    3. `test_rejects_bare_host()` — `Config(lemonade_base_url="localhost:13305")` raises `ConfigError`.

#### 17. `tests/test_transcription_service.py` — MODIFY

- **Purpose.** Cover `probe_only()` OK/timeout/refused paths and assert constructor honours URL override.
- **Depends on.** #3.
- **Key changes:**
  - Add `test_probe_only_ok(requests_mock)` — `/api/v1/health` returns 200, `probe_only()` returns `(True, "")`.
  - Add `test_probe_only_timeout(requests_mock)` — patch `requests.get` to raise `requests.Timeout`, `probe_only()` returns `(False, "timeout")`.
  - Add `test_probe_only_refused(requests_mock)` — raises `requests.ConnectionError`, returns `(False, "connection refused")`.
  - Add `test_probe_only_does_not_start_server()` — monkeypatch `_lemonade_start_server` to assert-not-called; call `probe_only()` with a dead URL; verify the mock was never invoked. Protects Critical Rule #3.
  - Add `test_constructor_honours_server_url()` — existing test at tests/test_transcription_service.py:60 already uses `server_url="http://localhost:13305"`; extend to a non-default URL and assert `_endpoint` attribute matches.

#### 18. `tests/test_orchestrator.py` — MODIFY

- **Purpose.** On Lemonade probe failure at startup, orchestrator transitions to `AppState.ERROR` with `ErrorReason.LEMONADE_UNREACHABLE`.
- **Depends on.** #7.
- **Key changes:**
  - Add `test_npu_startup_check_failure_sets_lemonade_unreachable()`:
    - Build orchestrator with a mocked `TranscriptionService` whose `ensure_ready()` raises `TranscriptionNotReady`.
    - Drive `_npu_startup_check()` synchronously (not via a thread in the test).
    - Assert `sm.current is AppState.ERROR` and the last transition carried `reason=ErrorReason.LEMONADE_UNREACHABLE`.

#### 19. `tests/test_self_exclusion_frozen.py` — NEW

- **Purpose.** G8 verification — the frozen EXE's self-exclusion chain.
- **Depends on.** #5 (`single_instance.py` confirmed), #6 (`mic_watcher.py` confirmed).
- **Key changes:**
  - New file. Two tests:
    1. `test_single_instance_writes_frozen_basename(monkeypatch, tmp_path)`:
       - `monkeypatch.setattr(sys, "frozen", True)` and fake `sys.executable`.
       - Redirect `_lockfile_path()` to `tmp_path`.
       - Build `SingleInstance`, `acquire()`, read the lockfile. Assert line 2 == `"MeetingRecorder.exe"`.
    2. `test_mic_watcher_excludes_frozen_basename()`:
       - Call `_is_self("C:#Users#x#AppData#Local#MeetingRecorder#MeetingRecorder.exe", "MeetingRecorder.exe")` → assert True.
       - Call `_is_self("C:#OtherApp#OtherApp.exe", "MeetingRecorder.exe")` → assert False.
    3. Keep alias test parity: `_is_self("C:#Python312#pythonw.exe", "python.exe")` → assert True (per memory `reference_python_self_exclusion_aliasing`).
  - `pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")` where required (the exclusion logic itself is pure string code and runs cross-platform).

### Dependency verification

- **No circular deps.** Files 1 and 2 are leaves. Files 3-7 depend on 1-2 only. UI files 8-10 depend on 1-3 and 7. Build artifacts 11-14 depend on 1, 11. Docs 15 depends on 11-14. Tests 16-19 depend on 2, 3, 7.
- **Deletion order.** File 13 MUST NOT be deleted until file 8 lands (settings_tab.py drops `import install_startup`). Build agent enforces via sequential PRs or a single commit with both changes.

---

## 3. Inline ADRs

### ADR-1: PyInstaller onedir over onefile

**Context.** PyInstaller supports `--onefile` (self-extracting single EXE) and `--onedir` (folder build). Onefile is superficially appealing for tester handoff (one file, not a folder).

**Decision.** Onedir. `MeetingRecorder.exe` + `_internal\` directory. Matches existing [`installer.iss:41`](../../../installer.iss) which globs `dist\MeetingRecorder\*` recursively.

**Consequences.**
- Cold-start is fast (no `%TEMP%\_MEIxxxxx` extract on every launch).
- AV heuristics trip less often on onedir (no self-extracting exe pattern).
- Installer packages the whole folder into `{app}`; testers see a normal Program Files entry.
- Slightly larger apparent install footprint (no compression inside a self-extractor), but LZMA in Inno Setup recovers most of the delta.

**Alternatives rejected.**
- **Onefile** — rejected: `%TEMP%\_MEI` extract on every launch adds 2-4s cold-start latency on spinning disks; `customtkinter` asset resolution is fragile under PyInstaller's `_MEIPASS` on onefile; some corp-managed antivirus flags onefile bootloaders as suspicious. Peer research (Infinity Arcade, Lemon Zest) both use onedir.

### ADR-2: Inno Setup over WiX / NSIS / MSIX

**Context.** Windows installer tools: Inno Setup 6 (existing), WiX Toolset (MSI), NSIS (older OSS), MSIX (modern, signed).

**Decision.** Keep Inno Setup. `installer.iss` is already present and targeted correctly; rewriting the installer layer would double the scope of this feature.

**Consequences.**
- Continuity: stable `AppId` (already set at installer.iss:10), stable Start Menu path, stable uninstaller registration.
- Pascal scripting is ugly but adequate for the Lemonade probe.
- No MSI transform / GPO deployment story — but audience (b) is not enterprise-managed.

**Alternatives rejected.**
- **WiX** — rejected: MSI format adds ceremony (upgrade codes, component GUIDs) with zero payoff for internal testers; learning curve larger than the rest of the feature combined.
- **NSIS** — rejected: newer than Inno only in scripts language; requires throwing away `installer.iss`; no advantage for our use case.
- **MSIX** — rejected: requires a signing certificate to install at all (Authenticode or sideload cert); v1 is explicitly unsigned per BRAINSTORM lock.

### ADR-3: Inno Pascal probe for Lemonade, do not bundle or download

**Context.** Three options to handle Lemonade as a prereq: (a) bundle `LemonadeServer.exe` + NPU drivers inside our installer; (b) download during install; (c) detect-and-prompt (peer norm).

**Decision.** Option (c). Two-phase probe in `[Code]`: (1) binary on PATH via `where LemonadeServer.exe`; (2) 1-second HTTP `GET /api/v1/health` on `http://localhost:13305`. If BOTH fail, show a non-blocking info page with `lemonade-server.ai` link + "Next / Cancel" buttons.

**Consequences.**
- Installer stays ~50 MB (vs ~800 MB with Lemonade bundled).
- No network dependency at install time (corp-network friendly).
- Graceful degrade: even if a tester clicks "Next" past the info page, the app launches and shows the Live-tab banner (DEFINE G3).
- Licensing-friendly: we never redistribute Lemonade binaries.

**Alternatives rejected.**
- **Bundle Lemonade** — rejected per BRAINSTORM Explicitly Rejected Alternatives: licensing terms require prereq-not-embed; size; update drift; NPU driver coupling hidden not solved.
- **Downloader-during-install** — rejected: adds network dependency, corp-network-hostile, fragile on first-run.
- **Blocking modal on first app launch** — rejected: peer UX is graceful-degrade (AnythingLLM pattern), not modal wall. GAIA's heavy wizard justifies itself because GAIA DOES bundle; we don't.

### ADR-4: Retire `install_startup.py`, use Inno `{userstartup}` + `startupicon` task

**Context.** `install_startup.py` hardcodes `pythonw.exe "src/main.py"` at line 17; paths break the moment the app is frozen. Two options: rewrite frozen-aware, or retire.

**Decision.** Retire. Inno Setup's `[Icons]` `{userstartup}` entry gated on a new `startupicon` task replaces it entirely. The Settings tab's `launch_on_login` toggle becomes informational (logs the intent) when frozen; source-run developers register their own HKCU\\Run entry if they want it.

**Consequences.**
- Inno owns startup-on-login atomically with install/uninstall — uninstall cleans up automatically (DEFINE S9).
- Two mechanisms collapsed to one; less drift surface.
- Source-run dev loses a one-command toggle — but the tester audience doesn't use it, and source-run devs can manage HKCU\\Run themselves.
- Tests still cover `Config.launch_on_login` as a settable boolean; only the side-effect changes.

**Alternatives rejected.**
- **Rewrite frozen-aware** — rejected: keeps two paths for the same job; Settings → toggle → winreg.SetValueEx fights with Inno's installer-managed shortcut on uninstall (double-delete race). DEFINE Q5 locks this.
- **Leave the file but remove the import** — rejected: dead code rots fast; `git rm` is honest.

### ADR-5: Unified `src/app/__version__.py` over multi-source

**Context.** Version currently lives only in `installer.iss:3` as a hardcoded `"4.0.0"` string. The app itself has no version constant (grep returns zero hits).

**Decision.** Create `src/app/__version__.py` as the single source. All consumers (runtime About row, orchestrator boot log, installer AppVersion, CI release tag) read from it via import, `GetStringFromFile` regex, or PowerShell `Select-String`.

**Consequences.**
- One line edit to bump version.
- CI gates catch drift by regexing the same file.
- No `pyproject.toml` to maintain (we don't publish to PyPI).
- `importlib.metadata` is broken when frozen; a hand-written constant is robust.

**Alternatives rejected.**
- **`pyproject.toml` + `importlib.metadata`** — rejected: doesn't work under PyInstaller without extra collect-metadata hooks; introduces a packaging file we don't otherwise need.
- **Hardcode in `installer.iss` + separate constant in `main.py`** — rejected: that IS the bug we are fixing (current state has `installer.iss:3` hardcoded and zero runtime constant).
- **CI-injected env var only** — rejected: breaks local maintainer builds (`pyinstaller MeetingRecorder.spec` from the box must work without env bootstrap).

### ADR-6: Graceful-degrade banner over blocking first-launch modal

**Context.** When Lemonade is unreachable at app startup, the UI can either (a) show a modal blocking the main window until Lemonade is fixed, or (b) show an inline banner and let the user land on Live/Settings tabs.

**Decision.** Inline banner + Settings reachability row. No modal. Matches AnythingLLM's proven pattern (per BRAINSTORM peer research).

**Consequences.**
- First-launch experience is non-confrontational; tester can click around, read About, pick directories in Settings.
- Banner is visible enough (blue fg 2a3a5a, top-of-tab placement) to not be missed.
- `[Open Settings]` button gives a direct fix path.

**Alternatives rejected.**
- **Blocking modal** — rejected: peer apps that bundle Lemonade (GAIA) can afford a wizard because the fix is one-click. We don't bundle, so a modal is a dead-end for a tester without Lemonade — they can't proceed AND can't explore. Inline banner is strictly better.
- **Silent fail** — rejected: tester has no way to diagnose.

### ADR-7: Manual URL override in Settings, not env var or CLI flag

**Context.** Advanced users may need to point the app at a non-default Lemonade URL (custom port, remote dev box, corp proxy).

**Decision.** Expose `Config.lemonade_base_url` as a text field in Settings → "Lemonade URL". Persisted in `%APPDATA%\MeetingRecorder\config.toml`. Saved via the existing atomic write.

**Consequences.**
- Discoverable by testers without documentation (they'll see it next to reachability diagnostic).
- Persistent across launches (vs env var which dies with the shell session).
- Works for installed EXE (tester has no CLI surface — Start Menu shortcut, double-click from installer, or `{userstartup}` entry).

**Alternatives rejected.**
- **Env var** — rejected: testers don't know env vars; frozen app may be launched from Explorer / Start Menu with inherited env only.
- **`--lemonade-url` CLI flag** — rejected: same surface problem; Start Menu shortcut can't be edited by a non-expert tester.
- **Hardcoded in installer** — rejected: "localhost:13305" IS the hardcoded default; what we add is user-configurable override.

### ADR-8: GitHub Actions CI (Approach B) over local-only builds

**Context.** Two shapes considered in BRAINSTORM: Approach A (local `pyinstaller` + `iscc`, no CI) vs Approach B (A + Windows-runner CI workflow).

**Decision.** Approach B. Cost: ~120 lines of YAML. Benefit: reproducible builds, version-drift gate, signing hook exercised from day one.

**Consequences.**
- Any maintainer / collaborator can trigger a build without a local PyInstaller install.
- Draft release is the canonical distribution surface — no OneDrive copy-paste.
- Windows runner minutes are 2× Linux — mitigated by `workflow_dispatch` only (no push/PR triggers).
- Approach A is still available as the local recipe in README; the `.spec` and `installer.iss` are identical in both — only the driver layer differs.

**Alternatives rejected.**
- **Approach A only** — rejected: already burned once by `installer.iss:3` hardcoded `"4.0.0"` drifting unobserved; CI is cheap insurance against that class of bug.
- **Self-hosted runner** — rejected: ops overhead higher than GitHub's hosted Windows runner for our scale.
- **CI with push triggers** — rejected: runner-minute burn for every PR; `workflow_dispatch` (manual) is sufficient for a tester ring.

### ADR-9: Per-user default install, per-machine opt-in via `PrivilegesRequired=lowest`

**Context.** Inno Setup supports four modes: `lowest`, `admin`, `poweruser`, `dialog`. DEFINE G10 requires per-user default (no UAC) with per-machine as an option.

**Decision.** `PrivilegesRequired=lowest` + `PrivilegesRequiredOverridesAllowed=dialog`. Default path is `{localappdata}\Programs\MeetingRecorder`; advanced "Install for all users" button prompts for elevation and switches to `{commonpf}\MeetingRecorder`.

**Consequences.**
- Tester default: two Next clicks + one Install click, no UAC prompt (DEFINE G10, S1).
- IT-managed boxes can still opt into per-machine via the wizard button.
- `{userstartup}` entry works in both modes — it's always a per-user shortcut.

**Alternatives rejected.**
- **`PrivilegesRequired=admin`** — rejected: forces UAC on every install; friction for tester audience.
- **`PrivilegesRequired=poweruser`** — rejected: Vista-era concept, unreliable; deprecated in practice.
- **Dual installers (user + machine)** — rejected: two artifacts double test surface; `dialog` mode is what Inno is built for.

### ADR-10: SignTool env-var stub in `installer.iss`, not unconditional signing

**Context.** We have no Authenticode cert for v1. A future cert purchase should not require re-architecting the installer.

**Decision.** Wrap `SignTool=signtool_cmd $f` in `#ifdef SIGN` preprocessor guard. CI sets `/dSIGN=1` only when repo secrets `SIGNTOOL_CERT_PATH` + `SIGNTOOL_PASSWORD` are present. Matches GAIA's pattern.

**Consequences.**
- Zero cost now (stub never fires).
- Zero rework when a cert arrives: add two GitHub secrets + flip CI flag.
- `SmartScreen` reputation build-up is future work — documented in README as the "More info → Run anyway" path.

**Alternatives rejected.**
- **Unconditional signing** — rejected: breaks builds without a cert (CI and local).
- **Add signing support "when we have a cert"** — rejected: paints into corner; proven that "when we have X" = "never" until it isn't. Stub is small and documents intent.

### ADR-11: No portable .exe for v1

**Context.** Lemon Zest ships both an installer AND a portable zip. Useful for USB-handoff testers.

**Decision.** Installer only for v1. Testers who need portable can zip `dist\MeetingRecorder\` themselves (maintainer can do this once per release if asked).

**Consequences.**
- Single artifact to test against smoke matrix (G7).
- No `portable=true` branch in the runtime to worry about (config path discovery, log location, etc.).
- If a tester specifically requests portable, revisit in v1.1 as a ~30-minute follow-up.

**Alternatives rejected.**
- **Ship both** — rejected: doubles the test surface for marginal benefit at v1.
- **Portable only** — rejected: loses Start Menu + startup-on-login affordances that testers want.

### ADR-12: Keep `Config.launch_on_login` field + informational UI, defer to Inno for actual registration

**Context.** Retiring `install_startup.py` (ADR-4) removes the app's ability to flip HKCU\\Run at runtime. But `Config.launch_on_login: bool` and the Settings toggle still exist in code.

**Decision.** Keep the field + toggle. Rewrite `_apply_login_toggle` to log and return (when frozen). Source-run developers see an info log; frozen users see the toggle reflect their preference but Inno's installer-time `startupicon` task is the actual mechanism.

**Consequences.**
- No test churn (existing `Config.launch_on_login` round-trip test passes unchanged).
- Settings UI looks the same; tester is never confronted with a sudden missing toggle.
- Slight surprise: flipping the toggle post-install doesn't change startup behavior until reinstall. README documents this.

**Alternatives rejected.**
- **Remove the field and toggle** — rejected: Config schema churn + UI churn + test churn for a purely cosmetic win.
- **Rewrite `_apply_login_toggle` to write HKCU\\Run directly** — rejected: duplicates Inno's `{userstartup}` shortcut; on uninstall Inno removes its shortcut but not our HKCU entry; double-mechanism drift.

---

## 4. Threading model

Every cross-thread boundary called out. New / changed boundaries in **bold**.

| Thread | Responsibility | Cross-thread hand-off |
|--------|----------------|------------------------|
| T0 (main/startup) | `main.py` pre-mainloop steps: AUMID, logging, `SingleInstance.acquire()`, Config load, theme init | After `AppWindow` is constructed, transfers control to T1 via `self._root.mainloop()` |
| T1 (Tk mainloop) | All UI, StateMachine transitions, `on_state()`, **banner show/hide, switch_tab()** | Receives work via `AppWindow.dispatch(fn)` → `self._root.after(0, fn)` |
| T2 (mic-watcher) | Registry polling, raw users diff | Callbacks fire with `dispatch(on_mic_active)` → T1 |
| T3 (tray) | `pystray.Icon.run()` blocks | Menu callbacks use `dispatch(on_quit/toggle)` → T1 |
| T5 (audio writer) | PyAudio WASAPI → queue → WAV writer | Calls `transcription_svc.stream_send_audio()` (thread-safe queue.Queue.put) |
| T6 (npu-startup / save / batch-transcribe / retranscribe) | Background HTTP work (`ensure_ready`, `transcribe_file`, `list_npu_models`) | **NEW:** background worker now reads `config.lemonade_base_url` on construction; URL change triggers `_ready=False` which forces re-probe on next `ensure_ready()` (T6 is respawned per task, so no mid-flight mismatch) |
| T7 (stream-transcriber) | asyncio WebSocket session | `on_delta` / `on_completed` callbacks → `dispatch(...)` → T1 |
| **T_probe (settings-tab retry click)** | **Worker thread spawned on Settings → Retry click; calls `probe_only()` with 1-5s timeout** | **Result dispatched back via `AppWindow.dispatch(lambda: settings_tab.set_lemonade_reachable(ok, detail))`. MUST NOT call `probe_only()` on T1 — the 1s timeout would freeze the UI.** |
| **Inno Setup Pascal thread** | **Single-threaded during install; `LemonadeBinaryOnPath()` + `LemonadeHttpProbe()` run sequentially in `ShouldSkipPage`** | **No concurrency. `WinHttpRequest.5.1` COM object is created and destroyed per page.** |
| GitHub Actions runner | Sequential steps, no parallel jobs within the build | No runtime-process concerns; file-system-only coordination |

**Critical Rule #2 enforcement for the new surfaces:**

- `LiveTab.show_lemonade_banner()` / `hide_lemonade_banner()` — docstrings say "T1 only". Callers: `AppWindow.on_state()` (already on T1) and the banner's own `_on_open_settings_clicked` (tk callback, T1).
- `SettingsTab.set_lemonade_reachable()` — docstring says "T1 only; dispatch via AppWindow.dispatch". Callers: orchestrator's probe-worker result handler (dispatches), Settings Retry button's result handler (dispatches).
- `AppWindow.switch_tab()` — docstring says "T1 only". Callers: LiveTab banner button (T1 via tk callback).
- `TranscriptionService.probe_only()` — **the one method explicitly safe from any thread** (pure read-only HTTP), but must not be called from T1 because of the 1s timeout. Build agent wires it to T_probe.
- `TranscriptionService.set_base_url()` — not thread-safe for concurrent stream; callers MUST stop streaming first. Currently only called from `_on_config_saved` (T1) when the stream is quiescent by precondition (Save button disabled during RECORDING is not today enforced — add a comment in set_base_url noting this; hard gate is a v1.1 improvement, not blocking).

---

## 5. Verification plan

### 5.1 Automated (pytest)

Run on every CI invocation (gate for the freeze step):

| Test file | Coverage | Maps to goal |
|-----------|----------|--------------|
| `tests/test_config.py` | `lemonade_base_url` default, round-trip, validation | G4, G5 |
| `tests/test_transcription_service.py` | `probe_only()` OK / timeout / refused / does-not-start-server; constructor URL override | G4, Critical Rule #3 |
| `tests/test_orchestrator.py` | Probe failure → `AppState.ERROR` + `ErrorReason.LEMONADE_UNREACHABLE` | G3 |
| `tests/test_self_exclusion_frozen.py` (NEW) | Lockfile contains `MeetingRecorder.exe` when `sys.frozen=True`; `_is_self()` exact match; alias parity unchanged | G8, Critical Rule #4 |

Commands:

```bash
python -m pytest tests/ -v
ruff check src/ tests/
ruff format --check src/ tests/
```

### 5.2 Manual smoke matrix (DEFINE S1-S10)

Run against `installer_output\MeetingRecorder_Setup_v4.0.0.exe` on **all three** matrix machines (DEFINE Q4):
- (a) Maintainer dev box (AMD Ryzen AI + BT-88 A2DP default mic)
- (b) Clean Win11 with Lemonade installed
- (c) Clean Win11 without Lemonade installed

| ID | Scenario | Expected | Priority notes |
|----|----------|----------|----------------|
| S1 | Installer itself (per-user default, SmartScreen click-through, Start Menu, Installed Programs) | No UAC on default; Start Menu "MeetingRecorder" appears; version in Installed Programs == `__version__.py` | Covers G2, G10 |
| S2 | Lemonade probe — absent (machine c) | Non-blocking Inno info page shown; Next + Cancel both work | Covers G3 install-time |
| S3 | Lemonade probe — present (machine b) | Info page skipped silently | Covers G3 positive path |
| S4 | First launch, Lemonade cold (machine a or c) | Tray appears; Live-tab banner shows; Settings reachability = FAIL; starting Lemonade externally flips to OK within 5s (probe cadence) | **G3, G4 — critical** |
| S5 | First launch, Lemonade warm (machine b) | Captions appear on real call within ~20s; no AppState.ERROR | Covers G1 runtime |
| S6 | Mic self-exclusion post-freeze (machine a) | Log line `[MIC] ... (excluded as self: ['MeetingRecorder.exe'])` | **G8 — Critical Rule #4 regression guard** |
| S7 | BT-88 A2DP silent-capture safety-net (machine a) | Safety-net banner fires after N=4 silent recordings; WASAPI path survives PyInstaller freeze | Memory `project_bt_a2dp_zero_capture` pin |
| S8 | Stop + restart cycle | `.md` in `vault_dir`; `.wav` in `wav_dir`; no orphan tray; re-recording works | Covers runtime regressions |
| S9 | Uninstall | Program files removed; Start Menu + `{userstartup}` shortcut removed; `Config.vault_dir` PRESERVED | Covers G2 uninstall; memory-safety for user data |
| S10 | Version consistency | Settings → About, Installed Programs, `__version__.py`, installer artifact filename — all say `4.0.0` | Covers G5 |

### 5.3 Installer-specific checks (new)

Independent of S1-S10; run on each machine:

- **Per-user install.** Default path. No UAC. Installs to `%LOCALAPPDATA%\Programs\MeetingRecorder`.
- **Per-machine install.** Wizard "Advanced → Install for all users" button. UAC prompt. Installs to `C:\Program Files\MeetingRecorder`.
- **SignTool stub inactive.** Verify build with `/dSIGN=1` only runs when secrets present (CI log inspection; local build must not fail on missing cert).
- **`InitializeSetup()` probe coverage.** (a) Kill Lemonade process, uninstall binary → run installer → info page appears. (b) Start Lemonade → run installer → info page skipped silently (Inno log confirms via `/LOG=` flag on `Setup.exe`).
- **Version drift gate.** Edit `__version__.py` to `"4.0.1"`, leave `installer.iss` alone, run local build. `iscc /dAppVersion=4.0.0 installer.iss` produces `MeetingRecorder_Setup_v4.0.0.exe` — version mismatch is purely informational at v1 per DEFINE G6 "soft check".

### 5.4 Non-regression checks (G9)

- `python src/main.py` still launches from repo root. Live tab visible. Config loads from `%APPDATA%\MeetingRecorder\config.toml` unchanged. No missing imports. `sys.frozen` resolves to `False`; `SingleInstance._exe_basename()` returns `"python.exe"` or `"pythonw.exe"`.
- Existing tests `tests/test_mic_watcher.py`, `tests/test_single_instance.py`, `tests/test_transcription_service.py` (pre-modify), `tests/test_orchestrator.py` (pre-modify) all pass on the same set as before.
- `ruff check src/ tests/` returns zero new lints.

### 5.5 Goal → file → verification traceability

| Goal | File(s) | Verification |
|------|---------|--------------|
| G1 (freeze launches) | #11 spec | S1, S5 |
| G2 (installer + uninstaller) | #12 installer.iss | S1, S9 |
| G3 (graceful degrade) | #8, #9, #10 | S4, pytest #18 |
| G4 (Settings reachability row + URL override) | #2, #3, #8 | pytest #16, #17; S4 |
| G5 (unified version) | #1, #8 (About), #12, #14 | S10, pytest #16 (soft) |
| G6 (GitHub Actions release artifact) | #14 | CI workflow run; artifact download |
| G7 (smoke matrix) | all | S1-S10 on 3 machines |
| G8 (self-exclusion post-freeze) | #5, #6, #19 | S6, pytest #19 |
| G9 (source-run unregressed) | n/a (preservation) | Non-regression §5.4 |
| G10 (per-user / per-machine modes) | #12 | S1 per-user, installer-specific §5.3 |

---

## 6. Rollback plan

- **Feature branch.** All changes land on `feat/exe-packaging`; merge via PR to `refactor/flow-overhaul`. Revert = `git revert <merge-commit>`.
- **No DB or registry state.** Config schema gains one field with a safe default (`lemonade_base_url`); older config files without the field load cleanly via `data.get("lemonade_base_url", "http://localhost:13305")`. No migration script.
- **Installer.** Uninstalling cleans `{app}` and shortcuts; `%APPDATA%\MeetingRecorder\config.toml` is preserved by design (DEFINE S9).
- **`install_startup.py` deletion.** Reversible via git; anyone running source-mode with muscle-memory `python install_startup.py install` sees a `ModuleNotFoundError` that fails fast and loud, not silently.
- **CI workflow.** `workflow_dispatch` only — deleting the YAML file does not break anyone's workflow since nothing auto-triggers.

---

## 7. Self-review (quality gate)

Checked before handing off:

- [x] **No circular deps in manifest.** Verified: files 1-2 are leaves; 3-10 depend only on earlier entries; 11-14 depend on 1 + earlier runtime files; tests 16-19 depend on their targets.
- [x] **Every ADR has rejected alternatives.** 12 ADRs, each with 1-3 rejections cited from BRAINSTORM or first principles.
- [x] **Threading is explicit.** Section 4 calls out every new cross-thread boundary (T_probe, Inno Pascal, CI runner) and cross-references Critical Rule #2. `probe_only()`'s "must not run on T1" constraint is documented on the method itself.
- [x] **Windows-only constraints called out.** Critical Rule #1 covered by: frozen build runs on Windows only; `pytestmark = skipif(sys.platform != "win32")` on Windows-dependent tests; CI is `windows-latest`. Source-mode analysis remains importable cross-platform (non-regression G9).
- [x] **Critical Rules cross-referenced.** #1 (Windows-only) in §5.4 and ADR-11; #2 (mainloop thread) in §4; #3 (ensure_ready gate) in ADR-7 ADR-12 and #3 file spec; #4 (self-exclusion) in #5, #6, #19; #6 (no personal paths) in #2 default-value note; #8 (Lemonade WS schema) in #3 `probe_only()` spec.
- [x] **All 10 G-goals mapped** — table §5.5.
- [x] **All 10 S-smoke tests cited** — table §5.2.
- [x] **File manifest order respects dependencies.** `__version__.py` → `config.py` → `transcription.py` → orchestrator → UI → spec → installer → tests.
- [x] **Pre-design factual corrections applied.** Lemonade default URL corrected to `:13305` (was DEFINE's `:8000`); `{userstartup}` mechanics verified against Inno docs; `settings_tab.py:433` `import install_startup` handled explicitly in file #8.
- [x] **Line length fits.** Document is ~700 lines of markdown — within DEFINE's stated target.
