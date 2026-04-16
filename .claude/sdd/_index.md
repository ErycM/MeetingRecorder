# AgentSpec 4.2 — SaveLiveCaptions

> 5-phase Spec-Driven Development workflow.
> *"Brainstorm → Define → Design → Build → Ship"*

---

## When to Use SDD vs Dev Loop

| Use SDD When | Use Dev Loop When |
|--------------|-------------------|
| Feature touches 3+ files | Single-file tweak |
| You want ADR-level traceability | Prototype or experiment |
| Will be revisited in 6+ months | One-off automation |
| Installer / packaging implications | Internal tooling |
| New audio or transcription feature | UI polish |

---

## Pipeline

```text
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   Phase 0    │───▶│   Phase 1    │───▶│   Phase 2    │───▶│   Phase 3    │───▶│   Phase 4    │
│  BRAINSTORM  │    │   DEFINE     │    │   DESIGN     │    │    BUILD     │    │    SHIP      │
│  (Explore)   │    │  (What+Why)  │    │    (How)     │    │     (Do)     │    │   (Close)    │
│  [Optional]  │    └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
└──────────────┘           │                   │                   │                   │
       │                   ▼                   ▼                   ▼                   ▼
       ▼              DEFINE_*.md         DESIGN_*.md        Code + Report       SHIPPED_*.md
  BRAINSTORM_*.md

       ◀─────────────────────────────────────────────────────────────────────────────────▶
                                    /iterate (any phase)
```

---

## Commands

| Command | Phase | Agent | Model |
|---------|-------|-------|-------|
| `/brainstorm` | 0 | brainstorm-agent | opus |
| `/define` | 1 | define-agent | opus |
| `/design` | 2 | design-agent | opus |
| `/build` | 3 | build-agent (SE engine: pytest + ruff) | sonnet |
| `/ship` | 4 | ship-agent | haiku |
| `/iterate` | any | iterate-agent | sonnet |

---

## Artifacts

| Artifact | Phase | Location |
|----------|-------|----------|
| `BRAINSTORM_<FEATURE>.md` | 0 | `.claude/sdd/features/` |
| `DEFINE_<FEATURE>.md` | 1 | `.claude/sdd/features/` |
| `DESIGN_<FEATURE>.md` | 2 | `.claude/sdd/features/` |
| `BUILD_REPORT_<FEATURE>.md` | 3 | `.claude/sdd/reports/` |
| `SHIPPED_<YYYY-MM-DD>.md` | 4 | `.claude/sdd/archive/<FEATURE>/` |

---

## Quick Start

```bash
/brainstorm "Add speaker diarization to live captions"
/define .claude/sdd/features/BRAINSTORM_SPEAKER_DIARIZATION.md
/design .claude/sdd/features/DEFINE_SPEAKER_DIARIZATION.md
/build .claude/sdd/features/DESIGN_SPEAKER_DIARIZATION.md
/ship .claude/sdd/features/DEFINE_SPEAKER_DIARIZATION.md
```

Or skip brainstorm if the problem is clear:

```bash
/define "Add a keyboard shortcut to toggle recording on/off"
```

---

## Folder Structure

```text
.claude/sdd/
├── _index.md                 # This file
├── features/                 # Active phases 0-2
├── reports/                  # Build reports (Phase 3)
├── archive/                  # Shipped features (Phase 4)
├── templates/                # Phase templates
└── architecture/             # WORKFLOW_CONTRACTS.yaml + project notes
```

---

## References

| | |
|---|---|
| Commands | `.claude/commands/workflow/` |
| Agents | `.claude/agents/workflow/` |
| Templates | `.claude/sdd/templates/` |
| Dev Loop | `.claude/dev/_index.md` |
| Knowledge base | `.claude/kb/` |
