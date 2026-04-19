# BRAINSTORM: AUTONOMOUS_E2E

> Can Claude Code (running in the user's Windows terminal) execute the LIVE_CAPTIONS end-to-end checklist itself, instead of handing it to the human? This is a **tooling-feasibility** brainstorm, not a feature spec.

**Status: Phase 0 — research complete, approaches drafted, decision pending.**
**Requested by user (2026-04-16):** "What would it take for Claude Code to execute the LIVE_CAPTIONS E2E tests autonomously, instead of handing a checklist to the user? Deep research expected."

**Related context:**
- Memory: `feedback_smoke_test_before_done.md` — never close a task on unit tests alone; run the actual app flow first. This brainstorm is the response to that feedback: if smoke-before-done is mandatory, Claude should be able to run smoke itself.
- Memory: `project_bt_a2dp_zero_capture.md` — user's Windows default mic is dead during real calls. Any harness must work with that reality (audio-injection path matters).
- CLAUDE.md Critical Rules 1 (Windows-only), 2 (Tk-thread contract), 3 (Lemonade readiness), 7 (`ENFORCE_NPU=True` is immutable).

---

## Problem statement

### What the human is being asked to do today

After every non-trivial streaming or audio change, the LIVE_CAPTIONS E2E checklist is: launch `python src/main.py`, open Zoom/Teams/Discord to trigger `MicWatcher`, speak for ~15 s then pause then speak again, watch the Live tab paint captions, watch the timer advance, watch the tray icon change state, stop via one of three routes (silence auto-stop, Stop button, tray menu), verify `RECORDING→SAVING→IDLE` in the logs, verify a non-empty `.md` landed under `Config.vault_dir`, verify the History tab shows the entry, verify `[STREAM] Event-type counts:` shows `delta >= 1 AND completed >= 2`, then repeat for the single-instance guard and the settings round-trip.

That checklist takes ~5–10 minutes of **focused human attention** per cycle. The memory `feedback_smoke_test_before_done.md` says this is mandatory before closing a task. In practice the user runs it once per meaningful change on their own machine. Any automation that reduces the attention cost — even partially — compounds across dozens of cycles.

### Why this is hard

Six distinct capabilities are required, and each has Windows-specific gotchas:

1. **Native-window interaction** — Click Stop in a CTk window, right-click a pystray icon, dismiss a Windows confirm dialog.
2. **Real mic trigger** — `MicWatcher` only fires when an *external* EXE opens a mic capture handle and Windows writes its `LastUsedTimeStart` under `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged`. Faking this without an EXE that actually calls the mic is non-trivial.
3. **Audio injection** — The user's default mic is often dead during real calls (BT-88 A2DP). A harness that depends on "speak into the microphone" defeats the point.
4. **Visual verification** — Confirming captions painted, timer advanced, tray icon turned red. Pure log-tailing misses UI rendering bugs.
5. **Time compression** — The default silence-autostop is 30 s (`_DEFAULT_SILENCE_TIMEOUT = 30` at `src/app/config.py:51`). That alone is tolerable; the 180 s mentioned in the original checklist is the `recording.py` constant `DEFAULT_SILENCE_TIMEOUT_S = 180.0` which is only used when the orchestrator does not override it. The orchestrator *does* override it from Config (`src/app/orchestrator.py:260`), so effective timeout is already config-driven. Good news.
6. **Log observability** — Claude Code already has `run_in_background` + tail + `Read`, so this one is free.

The interesting constraint is #2 and #3 together: we need something that opens a real mic handle (to trigger `MicWatcher`) *and* injects speech-shaped audio (so Lemonade Whisper produces non-hallucinated captions). Those two can be satisfied by one helper EXE or split across two paths.

### Why the current status quo bites

- "Smoke before done" is a blocking rule. If Claude can't run smoke, every non-trivial PR ends with "please verify manually" — which the memory explicitly says is not acceptable.
- Regressions are silent between manual runs. The `[STREAM] Event-type counts` diagnostic only tells you captions arrived *when someone triggered a recording*. If Claude could run one scripted cycle at the end of every `/build`, we'd catch the CLAUDE.md Rule 8 class of bugs (OpenAI-shaped payload to Lemonade) the moment they regress instead of weeks later.
- The WAV safety net (`transcription.py:265-329`) is exactly the kind of thing that breaks without anyone noticing until a meeting is lost. A scripted cycle would catch "batch fallback silently stopped saving" in seconds.

---

## Research findings

### Thread 1 — Anthropic ecosystem tooling for Windows desktop automation

**Computer use is a beta API, not a Claude Code CLI tool.** The [computer use tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool) describe a `computer_20251124` beta tool that takes `screenshot | left_click | type | key | scroll` actions over a virtual display. It requires a **beta header**, is invoked via the **Messages API**, and the canonical reference implementation runs inside a **Linux Docker container with Xvfb** ([anthropic-quickstarts/computer-use-demo](https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-demo)). It does not auto-connect to the physical Windows desktop of the machine running Claude Code — you'd have to build your own action-handler bridge that takes Claude's `left_click (x,y)` requests and forwards them to the local machine via pyautogui + `mss`. Doable, but it is a custom harness, not a built-in Claude Code capability. Also: `computer_20251124` is only available on `claude-opus-4-7` / `claude-opus-4-6` / `claude-sonnet-4-6` / `claude-opus-4-5` and requires an explicit `"computer-use-2025-11-24"` beta header — so it is not something we can invoke from an interactive Claude Code session today without Messages-API scaffolding.

**Windows-targeted MCP servers exist and are real.** Three mature options:

- **[CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP)** — "Featured as a Desktop Extension in Claude Desktop." Provides `Click`, `Type`, `Scroll`, `Move`, `Shortcut`, `Screenshot`, `Snapshot` (full UI-tree capture with element IDs), plus `App launcher`, `Shell`, `Clipboard`, `Registry`. Install: `uvx windows-mcp`. **Explicitly supports Claude Code CLI** via `claude mcp add --transport stdio windows-mcp -- <path>\uvx.exe windows-mcp`. Requires Python 3.13+. Typical action latency 0.2–0.9 s. Does **not** specifically claim pystray/tray-icon support — tray interaction would be coordinate-based via `Click` against the tray area.
- **[sandraschi/pywinauto-mcp](https://github.com/sandraschi/pywinauto-mcp)** — FastMCP 2.10 compliant, 54 tools including `automation_windows` (11 ops), `automation_elements` (14 ops incl. click, hover, text), `automation_mouse` (9 ops with human-in-loop approval), `automation_keyboard` (4 ops), `automation_visual` (screenshots, OCR, image detection). Built on pywinauto so it inherits pywinauto's **UIA backend** and can drive windows-native controls. Crucially, **pywinauto does not support Tkinter** ([pywinauto docs](https://pywinauto.readthedocs.io/en/latest/getting_started.html)) — CTk wraps Tk, so button-identity lookups will fail; we'd be reduced to coordinate clicks anyway.
- **[mario-andreschak/mcp-windows-desktop-automation](https://github.com/mario-andreschak/mcp-windows-desktop-automation)** — AutoIt-based, TypeScript wrapper, mouse/keyboard/window management. Less polished than Windows-MCP but viable.

**Claude Code hooks** ([hooks reference](https://code.claude.com/docs/en/hooks)) expose `SessionStart`, `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop` lifecycle events with optional `async: true`. These could power a test harness that auto-runs smoke at Stop — e.g., when `/build` finishes, fire a smoke script — but hooks are one-shot shell commands, not a full automation layer. They are an orchestration mechanism on top of whichever automation we actually pick.

### Thread 2 — Pure-Python desktop automation on Windows

- **pywinauto** supports `win32` and `uia` backends but **explicitly does not support Tkinter/CustomTkinter**. Tk's widgets are rendered by Tk, not Win32 common-controls, and they do not expose UIA properties unless the app wires UIAutomation providers itself (we don't). The legacy `live_captions.py` in this repo used `uiautomation` to scrape *Microsoft's* Live Captions, not our own Tk app — that's a different scenario entirely. Implication: we can't identify our CTk buttons by name/AutomationId; we'd have to resolve them by window + coordinate.
- **pyautogui** — standard pip-install, works everywhere, clicks at absolute `(x, y)`. Known DPI scaling issues on Windows 10/11 laptops ([asweigart/pyautogui#31](https://github.com/asweigart/pyautogui/issues/31), [#110](https://github.com/asweigart/pyautogui/issues/110)). CustomTkinter auto-enables DPI awareness per [CTk wiki](https://github.com/TomSchimansky/CustomTkinter/wiki/Scaling), which helps if — and only if — the harness does `ctypes.windll.shcore.SetProcessDpiAwareness(2)` as well, otherwise reported coords and real coords disagree.
- **`uiautomation` Python package** — supports UIAutomation for MFC / WinForms / WPF / Qt / Chrome / Electron. CustomTkinter (Tk-based) is **not** in that list. This is the same dead end pywinauto hits.
- **`mss`** — [BoboTiG/python-mss](https://github.com/BoboTiG/python-mss), ctypes-based, 30-40× faster than PIL ImageGrab. Ideal for screen capture to a PNG that Claude can `Read` multimodally.

**Net:** for our CTk app, any native-window automation layer collapses to "pyautogui clicks at screen coordinates + mss screenshots for verification." That works on a single dev box if we pin window geometry, but it is brittle to DPI changes and requires the window to be on the primary monitor (pyautogui doesn't reliably drive secondary monitors per the same issues above).

### Thread 3 — Audio injection / bypass

**Virtual audio cables are real but require driver install.**

- **[VB-CABLE](https://vb-audio.com/Cable/)** — free, runs on Win10/11 x64 and ARM64. Install is a signed driver `.exe` **requiring admin + reboot**; the install is GUI-only by default with no documented silent/`/S` switch. Once installed, a VB-CABLE "CABLE Input" appears as a playback device and "CABLE Output" as a recording device. Any WAV played to "CABLE Input" shows up on "CABLE Output" — which the app can capture via `Config.mic_device_index = <vb_cable_index>`.
- **Open-source alternatives**: [VirtualDrivers/Virtual-Audio-Driver](https://github.com/VirtualDrivers/Virtual-Audio-Driver) and [JannesP/AudioMirror](https://github.com/JannesP/AudioMirror) — both WDM drivers requiring signed-driver install. Same install-friction problem.
- **Playback side**: Once a virtual cable exists, `sounddevice.play(audio_data, device="CABLE Input")` works cleanly per [sounddevice docs](https://python-sounddevice.readthedocs.io/en/0.4.1/usage.html). No further wiring needed.

**The test-hook alternative (recommended by the research).** Our `DualAudioRecorder` already accepts `Config.mic_device_index` / `Config.loopback_device_index` overrides (`src/audio_recorder.py:54-110`), and the audio pipeline calls `_resolve_mic_device` / `_resolve_loopback_device` on every `start()`. Adding a narrow *test-only* capability — `DualAudioRecorder.set_test_wav_source(path)` that replaces the PyAudio callback with a thread that reads PCM16 chunks from a WAV — would bypass WASAPI entirely. Pros: deterministic, no driver install, CI-friendly. Cons: skips the very thing we most want to verify (WASAPI capture works). This is the "make the app more testable" path.

**Hybrid:** use the existing `Config.mic_device_index` override to point at a virtual cable, and `sounddevice.play()` a canned WAV to the cable's input side. Zero src-code changes; the test harness is 100% external. The only setup cost is the one-time VB-CABLE install.

### Thread 4 — External-app mic trigger

`MicWatcher` fires when it sees `LastUsedTimeStart` change for a non-self EXE under `HKLM\...\CapabilityAccessManager\ConsentStore\microphone\NonPackaged\<path-with-#-separators>` ([src/app/services/mic_watcher.py](../../../src/app/services/mic_watcher.py)). Two ways to trigger it:

1. **Launch a tiny helper EXE that opens a mic handle** — a 5-line `sounddevice.InputStream(...)` script run from a **separate `python.exe`** (not `pythonw.exe`, because `MicWatcher` aliases those as "self" per `reference_python_self_exclusion_aliasing.md`). Windows writes the registry entry automatically. When the helper exits, Windows writes `LastUsedTimeStop`. The harness controls recording duration precisely.
2. **Direct registry write** — tampering with `CapabilityAccessManager` requires admin and is fragile (Windows may reconcile). Not recommended.
3. **Drive a real external app (Teams/Discord) via UI automation** — heaviest and flakiest. Not worth it.

Option 1 is the clean path. The helper EXE must live under a path that *isn't* aliased to SaveLiveCaptions' own exclusion (lockfile-based per `_read_lockfile_exclusion`), so placing it under `tests/harness/mic_trigger.py` and running it via `subprocess.Popen([sys.executable, ...])` from a separate interpreter works.

### Thread 5 — Time compression

Already available. `Config.silence_timeout` is persisted in `config.toml` with default 30 (not 180). The orchestrator passes it into `RecordingService` at construction (`src/app/orchestrator.py:260`) and hot-reloads on settings save (`src/app/orchestrator.py:818`). A harness can write a test config with `silence_timeout = 5` before launching, and auto-stop fires in 5 s. **No `src/` changes required.**

### Thread 6 — Realistic cost-benefit matrix

| Dimension | A: Status quo | B: Test-mode flags | C: Full GUI auto + VB-CABLE | D: Windows-MCP | E: Computer-use API |
|---|---|---|---|---|---|
| One-time setup | zero | small (add env-var hooks) | medium (VB-CABLE install + harness scripts) | small (`uvx windows-mcp` + `claude mcp add`) | large (build Windows action handler, beta header, separate API billing) |
| Fidelity vs real user flow | n/a | low (bypasses WASAPI + UI + tray) | high (real WASAPI, real UI click, real tray) | high (real UI click via OS-level events) | high (screenshot + click loop) |
| Flakiness risk | n/a | very low | medium (DPI, window position) | medium (LLM coordinate accuracy) | high (LLM coord accuracy + latency) |
| Per-cycle attention cost | 5–10 min | ~20 s | ~45 s | ~60 s | slow (beta) |
| Works in CI headless | n/a | yes | no (tray+CTk need a desktop) | no | no (needs Xvfb+Linux) |
| LOC added to repo | 0 | ~150 (narrow test hooks) | ~300 (harness + WAV fixtures) | ~50 (just the MCP call scripts) | ~400 (full agent-loop bridge) |

Key takeaway: **B and C+D are the only pragmatic options for local dev today**. E is premature.

---

## Proposed approaches

### Approach A — Status quo (baseline, listed for completeness)

**Summary:** Keep the current manual checklist. Claude produces diffs; the user runs `/build`; the user runs the app manually; the user reports back.

**Fits into:** both paths (no change).

**Risks:** attention cost persists; regressions leak between cycles; `feedback_smoke_test_before_done.md` continues to be honored by human effort only.

**Benefits:** zero engineering. It is the current state. This is what we improve against.

### Approach B — Test-mode env vars + Bash-driven smoke harness (pragmatic middle ground)

**Summary:** Add a small set of **env-var-gated** test hooks to let Claude drive the app from Bash alone, with zero driver installs and zero GUI automation. The harness writes a short-timeout config, launches the app in background, injects mic activity + audio from outside, tails the log, and asserts on log lines + the produced `.md` file.

**Fits into:** v3 pipeline only. No legacy LC path impact.

**Concrete shape:**

- `tests/harness/run_smoke.py` — entry point. Reads `SLC_SMOKE_CONFIG=tests/harness/smoke_config.toml`, starts `python src/main.py` via `subprocess.Popen` with a temp `%APPDATA%` redirect so it can't clobber real config.
- `tests/harness/smoke_config.toml` — pins `silence_timeout = 5`, points `vault_dir` + `wav_dir` at a temp dir, pins `mic_device_index` / `loopback_device_index` to **VB-CABLE indices** *iff* the cable is installed, otherwise to a safe default. No test-only code in `src/`.
- `tests/harness/mic_trigger.py` — 10-line script that opens a `sounddevice.InputStream()` for N seconds, causing `MicWatcher` to fire. Run it from a **separate `python.exe`** so `MicWatcher`'s self-exclusion does not filter it.
- `tests/harness/audio_injector.py` — `sounddevice.play(wav, device="CABLE Input")` to push prerecorded speech into VB-CABLE (when installed). Falls back to "no audio, assert only that the WAV safety-net saved an empty file" when the cable is absent.
- `tests/harness/assertions.py` — reads the `.log` file, asserts `RECORDING → SAVING → IDLE`, asserts `[STREAM] Event-type counts` has `delta >= 1 AND completed >= 2`, asserts a `.md` file appeared under the temp vault, asserts the transcript text contains N>0 chars.
- **Optional narrow test hook** in `src/`: add `SLC_MIC_SIMULATE=1` env var which, if set, makes `MicWatcher` emit one `on_mic_active()` after 2 s without registry polling. Gated by `if os.environ.get("SLC_MIC_SIMULATE"):` at `MicWatcher.__init__`. This lets the harness work even without a virtual cable / admin install. Must **never** be shipped in the Inno Setup installer — see constraint below.

**Dependencies added:** none new for the runtime (still `sounddevice` which is already transitively available). `tests/harness/` is test-only.

**Complexity:** ~150 LOC total, all under `tests/harness/`. One narrow env-var check in `src/app/services/mic_watcher.py` (~5 lines). Installer guard: add an Inno Setup pre-compile check that rejects builds where `SLC_MIC_SIMULATE` or equivalent appears in env, or simpler — use a compile-time constant `_ALLOW_TEST_HOOKS = False` that the harness flips via a monkey-patch at test import time, so no env var lives in production.

**Risks:**
- **Test mode = a new code path with its own blast radius.** Any env-var hook is a potential backdoor. Mitigation: the hook is a one-line `if os.environ.get(...) and _ALLOW_TEST_HOOKS: emit_fake_event()` branch and `_ALLOW_TEST_HOOKS` is flipped only by `tests/conftest.py`. In installer builds `_ALLOW_TEST_HOOKS` stays `False`; the env var is a no-op in production.
- **Bypass fidelity.** If we go the simulated-mic path, we don't exercise the real registry watcher. Mitigation: keep both — the harness tries real mic trigger first (launching `mic_trigger.py` as a separate process) and falls back to `SLC_MIC_SIMULATE=1` only when registry access is unavailable (CI, or admin not granted).
- **CTk UI not verified.** We assert captions via `[STREAM] Event-type counts` in logs; we don't see them paint. Mitigation: Approach B pairs cleanly with `mss` screenshots + Claude `Read` to visually verify captions painted — that's basically free once `run_in_background` is active.
- **BT-88 situation.** User's default mic is dead. Pinning `mic_device_index` to the VB-CABLE endpoint in `smoke_config.toml` sidesteps this — the override was added for exactly this kind of problem per `.claude/kb/windows-audio-apis.md`.

**Benefits:**
- Almost no runtime changes (one env-var branch in `MicWatcher`, one compile-time constant).
- Works today, no driver install required (in the `SLC_MIC_SIMULATE` path).
- Directly reuses existing `Config.silence_timeout`, `Config.mic_device_index`, WAV safety net, batch fallback — all are the real code paths.
- Fast (5 s silence timeout + ~5 s model load + ~10 s transcribe = ~20 s per cycle).
- Works within Claude Code's existing `run_in_background` + `Read` + log-tailing model; no MCP or API beta required.
- Every assertion in the harness is something the manual checklist already checks — zero new concepts for the user.
- Matches constraint "Isolate test-only code in `tests/harness/`" exactly.

**What to verify in `/define`:**
- Is `SLC_MIC_SIMULATE=1` an acceptable hook, or should we require VB-CABLE + real registry trigger?
- Is the `_ALLOW_TEST_HOOKS` compile-time constant sufficient guard, or do we want a build step that strips the branch entirely?

### Approach C — Full GUI automation via Windows-MCP + VB-CABLE (high-fidelity)

**Summary:** Install [Windows-MCP](https://github.com/CursorTouch/Windows-MCP) as an MCP server in Claude Code and install VB-CABLE on the dev box. Claude drives the real UI (clicks Stop, right-clicks tray, opens Settings) via Windows-MCP's screenshot+click cycle while the audio-injector streams PCM16 from a canned WAV to the virtual cable. No test hooks in `src/` at all — everything is external.

**Fits into:** v3 pipeline; no `src/` changes.

**Concrete shape:**

- One-time user setup: `uvx windows-mcp`, `claude mcp add --transport stdio windows-mcp -- %LOCALAPPDATA%\uv\bin\uvx.exe windows-mcp`, install VB-CABLE with admin + reboot.
- `tests/harness/run_smoke_mcp.py` — same as B but every UI interaction (click Stop, right-click tray) is a Windows-MCP call. Same `sounddevice.play(..., device="CABLE Input")` for audio.
- Visual verification: `Snapshot` action returns the UI tree with element IDs for apps that expose UIA. Our CTk window won't expose UIA, so we fall back to `Screenshot` + Claude multimodal `Read`. Acceptable for ad-hoc verification but is the weakest link.

**Dependencies added:** `windows-mcp` (installed globally via `uvx`), VB-CABLE driver (installed globally), neither lives in the repo.

**Complexity:** ~300 LOC harness + 1 driver install + 1 MCP registration. Harness is higher than B because every UI interaction goes through a tool call and needs error handling. Coordinate stability requires pinning window geometry at launch (CTk supports `geometry("WxH+x+y")`).

**Risks:**
- **CTk + UIA mismatch.** pywinauto / uiautomation / Windows-MCP's Snapshot all rely on UIA properties; CTk exposes Tk widgets which don't participate in UIA. Button identification falls back to coordinates or to Claude "visually" finding the button from a screenshot. The latter is slow and DPI-sensitive.
- **DPI scaling** — per [pyautogui issue #110](https://github.com/asweigart/pyautogui/issues/110), laptop displays misreport coordinates without explicit `SetProcessDpiAwareness(2)`. Mitigation: both CTk and the harness must set this.
- **Admin-grant friction.** VB-CABLE requires admin + reboot once per machine. Blocks CI and new-dev-box onboarding.
- **Latency.** Windows-MCP typical 0.2–0.9 s per action; a full smoke cycle is ~60 s incl. LLM click-planning. Fine for local dev, too slow for a tight loop.
- **MCP install churn.** Windows-MCP needs Python 3.13+ which may conflict with other envs on the dev box. `uvx` mitigates this with isolated envs.

**Benefits:**
- **Highest fidelity** — we click the real Stop button, we right-click the real tray icon, we exercise the full stack end to end.
- Zero test-only code in `src/`. Installer can't accidentally ship a backdoor because there is no backdoor.
- Visual regression detection: Claude `Read`s the screenshot and can say "the timer wasn't visible" even if the log claims success.
- Reuses `windows-mcp` for *other* tasks (installing apps, debugging, file ops), not just this smoke test — better amortization than a one-off harness.

**What to verify in `/define`:**
- Is the user OK with the one-time VB-CABLE install (admin + reboot)?
- Is 60 s/cycle acceptable, or does this need to be under 20 s for Approach C to be chosen?
- Do we want Windows-MCP regardless of this decision (general-purpose tool), or only as part of this smoke harness?

### Approach D — Computer-use API via dedicated Windows action handler (research-heavy)

**Summary:** Build a Python action-handler that bridges Claude's `computer_20251124` tool calls to local Windows via pyautogui + `mss`. Run it as a separate loop driven by the Messages API (not Claude Code), invoked from Claude Code via a shell command. Claude-the-orchestrator tells Claude-the-smoke-runner "run the LIVE_CAPTIONS checklist"; the latter performs the screenshot+click cycle on the local Windows desktop.

**Fits into:** outside `src/`; would live in `tests/harness/computer_use/`.

**Risks:**
- **Requires Messages API access with the correct beta header** — separate API call not through Claude Code, separate billing.
- **High latency** (per Anthropic's own docs: "current computer use latency for human-AI interactions may be too slow").
- **Reference implementation is Linux/Xvfb.** Windows port needs writing.
- **LLM-driven click accuracy** is bound by the same DPI / coordinate scaling issues as Approach C, plus extra coordinate-scaling math the docs call out explicitly.
- **Duplicative with Approach C** — Windows-MCP solves the same problem with less glue.

**Benefits:**
- If Anthropic adds first-class Windows computer-use support in Claude Code, this becomes the native way.
- Captures screenshots + reasoning trace as a nice-to-have debug artifact.

**Verdict:** not worth pursuing in 2026 for our use case. Windows-MCP is strictly dominant today.

### Approach E — Hybrid: B + visual spot-checks via `mss` + multimodal `Read`

**Summary:** Do Approach B's log-driven smoke harness, but whenever an assertion cares about "did the UI actually paint," take an `mss.grab()` screenshot to a temp PNG and have Claude `Read` it multimodally and describe what it sees. No interactive control (no clicking), just *observation*.

**Fits into:** extends Approach B.

**Example timeline:**

1. Harness launches app (B).
2. Harness launches `mic_trigger.py` to fire `MicWatcher` (B).
3. Harness plays WAV to default mic via VB-CABLE *or* bypasses via `SLC_MIC_SIMULATE` (B).
4. **New:** at T+3 s the harness captures the full screen via `mss`, saves to `%TEMP%\smoke_screenshot_T3.png`. Claude `Read`s it and verifies "CTk window is visible, Live tab is active, caption text is present."
5. **New:** at T+8 s (after stop), captures again, verifies "History tab shows a new row."
6. Log assertions as in B.

**Complexity:** ~30 LOC on top of B. No `src/` changes beyond B.

**Benefits:**
- Covers the UI-rendering gap without needing real clicks.
- Works inside Claude Code's built-in `Read` tool — no MCP install, no API beta.
- Cheapest incremental fidelity on top of the log-driven harness.

**Risks:**
- Observation-only — we can't actually click Stop from this path, only verify the app is in a given state. Stop is triggered via silence auto-stop (time compression) or via a separate CLI kill.
- Window must be on screen (not minimised); harness should `geometry(...)` + `lift()` it via a narrow test hook, or use `win32gui.SetForegroundWindow` from outside.

**Verdict:** **this is the pragmatic sweet spot.** B + E gets us log-level + visual-level assertions with no driver install, no MCP setup, no API beta.

---

## KB validations

- **[.claude/kb/windows-audio-apis.md](../../../.claude/kb/windows-audio-apis.md)** — already documents `Config.mic_device_index` / `loopback_device_index` overrides and `list_input_devices()` WASAPI-filtering. Approach B/C reuse those exact overrides by writing `mic_device_index = <VB-CABLE index>` into a test config. KB update: if we add `SLC_MIC_SIMULATE`, document it alongside the override section as "test-only, requires `_ALLOW_TEST_HOOKS=True`."
- **[.claude/kb/windows-system-integration.md](../../../.claude/kb/windows-system-integration.md)** — documents `MicWatcher`'s CapabilityAccessManager polling and the python.exe ↔ pythonw.exe self-exclusion aliasing (`reference_python_self_exclusion_aliasing.md`). The harness must run `mic_trigger.py` via a path that does *not* match the lockfile exclusion. Add a note: "the test harness launches a separate interpreter whose EXE path is deliberately not in the lockfile."
- **[.claude/kb/lemonade-whisper-npu.md](../../../.claude/kb/lemonade-whisper-npu.md)** — unchanged. All approaches still go through `TranscriptionService.ensure_ready()` → real Lemonade → real Whisper on NPU. `ENFORCE_NPU=True` stays untouched (Rule 7).
- **[.claude/kb/realtime-streaming.md](../../../.claude/kb/realtime-streaming.md)** — unchanged. The harness inherits whatever streaming fix ships from `BRAINSTORM_LIVE_CAPTIONS.md`.
- **[.claude/rules/python-rules.md](../../../.claude/rules/python-rules.md)** — threading contract and test-skipif conventions already support the harness pattern; new harness scripts get `pytestmark = pytest.mark.skipif(sys.platform != "win32", ...)` per the rules.

---

## Open questions for /define

*(Ranked by blocker priority.)*

### Must answer before `/design`

1. **Is the user willing to install VB-CABLE once on the dev box (admin + reboot)?** Yes → Approach C or B+C hybrid is viable. No → Approach B with `SLC_MIC_SIMULATE` is the only path, and we skip WASAPI fidelity.

2. **Is a single narrow env-var hook (`SLC_MIC_SIMULATE`) acceptable in `src/`, guarded by a compile-time `_ALLOW_TEST_HOOKS=False` constant?** Or must `src/` stay 100% test-free? The latter forces Approach C (driver install required, no shortcut).

3. **What's the target per-cycle time budget?** If < 30 s → B (log + mss) fits. If 30–90 s OK → C (full Windows-MCP) fits. If > 90 s is fine, D can be entertained.

4. **Does the smoke harness need to verify UI paint, or is log-only sufficient?** Log-only is cheapest (pure B). Visual verification via `mss` + `Read` is ~30 LOC more (E). Real clicks require C.

5. **How often should the harness run?** Once per `/build`? Once per `/commit`? Claude Code hook at `Stop` that auto-runs smoke at end of task? This determines how fast "fast enough" needs to be.

### Nice to resolve in `/define`, but not blocking

6. **Install Windows-MCP regardless?** It's a general-purpose tool with utility beyond this harness. Recommend yes, independent of the smoke decision.

7. **Screenshot storage policy.** Screenshots of the dev desktop may include sensitive data. Harness should save to a scoped temp dir and clean up on exit.

8. **Where does the smoke fixture live?** `tests/harness/fixtures/spoken_meeting.wav` (~15 s of real speech, CC0) — reusing `tests/fixtures/sample_meeting.wav` is not sufficient because that's a synthetic sine-burst; Whisper will hallucinate on it.

9. **CI scope.** Out of scope per the brief, but note: B's log-only mode could run on a GitHub Windows runner with no audio/UI → covers config + startup but not capture + UI. C cannot run headless.

10. **Install-path safety.** Inno Setup installer must `grep` the built EXE for `_ALLOW_TEST_HOOKS = True` and refuse to build if found — a compile-time guard.

---

## Recommendation

**Pursue Approach B + E (log-driven harness with mss+Read visual spot-checks) as the proof-of-concept; leave C as a second-wave upgrade once B proves its value.**

Concrete 1-2 day PoC scope:

- Day 1 (half):
  - `tests/harness/run_smoke.py` — launches `python src/main.py` in background with a temp config (`silence_timeout=5`, temp vault), tails logs.
  - `tests/harness/mic_trigger.py` — opens a mic stream for 8 s from a separate `python.exe`, exits; proves `MicWatcher` fires.
  - `tests/harness/assertions.py` — parses the last 200 log lines, asserts `RECORDING→SAVING→IDLE`, asserts `[STREAM] Event-type counts` has `delta>=1 AND completed>=2`, asserts a non-empty `.md` appeared.
- Day 1 (other half):
  - `tests/harness/audio_injector.py` — plays `fixtures/spoken_meeting.wav` to the **default playback device**, so WASAPI loopback picks it up. This works without VB-CABLE.
  - Glue them together: smoke returns 0 on success, 1 with a one-line failure reason on failure.
- Day 2:
  - Add `mss.grab()` at T+3 s and T+stop; save PNGs to `%TEMP%\slc_smoke\`.
  - Claude (in the outer loop running `/dev` or `/build`) `Read`s those PNGs to verify captions painted and History updated.
  - Wire up a `.claude/hooks.json` `Stop` hook that runs `tests/harness/run_smoke.py` after every `/build` completion.

Success criterion for the PoC: Claude Code runs `bash tests/harness/run_smoke.py`, sees exit 0, reads two screenshots, and confidently says "LIVE_CAPTIONS flow verified, timer painted, caption text present, History row added" — without any human keystrokes after `/build`.

**If the PoC succeeds:** promote the harness to a required `/commit` pre-check. Consider layering Windows-MCP (Approach C) later for interactive click-to-stop verification and settings round-trip.

**If the PoC fails to trigger `MicWatcher` from a separate `python.exe`:** fall back to a narrow `SLC_MIC_SIMULATE=1` env hook in `MicWatcher` (guarded by `_ALLOW_TEST_HOOKS`). Document it in `.claude/kb/windows-system-integration.md` in the same PR.

**Out of scope for this brainstorm:** CI, Messages-API computer-use (D), any `src/` changes beyond at most one narrow env-var hook. We're answering "can it work at all" — the answer is "yes, with a ~150-LOC harness that lives entirely under `tests/harness/`."

Hand this document to `/define`. Expected DEFINE deliverables:
1. Answer Q1 (VB-CABLE yes/no), Q2 (env-var hook yes/no), Q4 (log-only vs +visual).
2. Pick PoC scope exactly (day-1 half, day-1 full, or 2-day).
3. Decide whether the harness is invoked manually (`bash tests/harness/run_smoke.py`) or auto-fired by a Claude Code Stop hook.
4. Decide CI scope for a later iteration (explicitly deferred; mentioned only so `/design` knows the harness should keep CI in mind even if not ship-gated).

---

## Sources

- **Claude Code hooks reference** — [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks)
- **Claude computer-use tool docs** — [platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
- **Computer-use demo (Linux/Docker reference implementation)** — [anthropic-quickstarts/computer-use-demo](https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-demo)
- **Windows-MCP (CursorTouch)** — [github.com/CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP)
- **pywinauto-mcp (sandraschi)** — [github.com/sandraschi/pywinauto-mcp](https://github.com/sandraschi/pywinauto-mcp)
- **pywinauto docs (getting started, backends, Tkinter support)** — [pywinauto.readthedocs.io/en/latest/getting_started.html](https://pywinauto.readthedocs.io/en/latest/getting_started.html)
- **Microsoft WinAppDriver (maintenance status)** — [github.com/microsoft/WinAppDriver/issues/1103](https://github.com/microsoft/WinAppDriver/issues/1103)
- **python-uiautomation (yinkaisheng)** — [github.com/yinkaisheng/Python-UIAutomation-for-Windows](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)
- **python-mss (screenshot library)** — [github.com/BoboTiG/python-mss](https://github.com/BoboTiG/python-mss)
- **pyautogui DPI issue #31** — [github.com/asweigart/pyautogui/issues/31](https://github.com/asweigart/pyautogui/issues/31)
- **pyautogui DPI issue #110** — [github.com/asweigart/pyautogui/issues/110](https://github.com/asweigart/pyautogui/issues/110)
- **CustomTkinter scaling wiki** — [github.com/TomSchimansky/CustomTkinter/wiki/Scaling](https://github.com/TomSchimansky/CustomTkinter/wiki/Scaling)
- **VB-CABLE (VB-Audio)** — [vb-audio.com/Cable](https://vb-audio.com/Cable/)
- **Open-source virtual audio drivers (Virtual-Audio-Driver / AudioMirror)** — [github.com/VirtualDrivers/Virtual-Audio-Driver](https://github.com/VirtualDrivers/Virtual-Audio-Driver), [github.com/JannesP/AudioMirror](https://github.com/JannesP/AudioMirror)
- **python-sounddevice usage (device selection)** — [python-sounddevice.readthedocs.io/en/0.4.1/usage.html](https://python-sounddevice.readthedocs.io/en/0.4.1/usage.html)
- **CapabilityAccessManager registry forensics** — [medium.com/@cyber.sundae.dfir/capabilityaccessmanager-db-deep-dive-part-1](https://medium.com/@cyber.sundae.dfir/capabilityaccessmanager-db-deep-dive-part-1-ff49f69c58af)
- **pystray docs (menus)** — [pystray.readthedocs.io/en/latest/usage.html](https://pystray.readthedocs.io/en/latest/usage.html)

---

_Drafted 2026-04-16 for SDD Phase 0. This is a feasibility memo, not a feature spec — /define should pick the PoC scope and flip the remaining open questions._
