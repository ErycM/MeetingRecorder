---
name: deep-research
description: "Thorough investigation of a library, API, or problem. Use for unfamiliar territory like new Lemonade versions, new audio APIs, new Whisper features. Triggers on: /deep-research, 'research this', 'investigate'."
disable-model-invocation: false
allowed-tools: WebFetch, WebSearch, Read, Grep, Bash
---

# /deep-research — Investigate a Topic End-to-End

## Usage

```
/deep-research <topic or question>
```

## Process

1. **Frame the question**. What do we actually need to decide or learn? Write it in one sentence.
2. **Check existing KB** (`.claude/kb/*.md`). If we already have a relevant note, start there; don't duplicate.
3. **Parallel sources** — use at least three:
   - Official docs (context7 / ref-tools for library docs)
   - GitHub (SDK source, issues, recent commits)
   - Primary sources (Microsoft Learn for Windows APIs, OpenAI spec for Realtime, Lemonade repo for NPU specifics)
4. **Read the code too**. Opening `site-packages/openai/_resource.py` often answers more than docs.
5. **Synthesize**:
   - Short summary (3–5 bullets)
   - A decision/recommendation (not just options)
   - Gotchas / non-obvious behaviors
   - Links to sources

## Output format

```markdown
# Research: <topic>

## TL;DR
<2-3 sentences — the answer>

## Key findings
- ...

## Recommendation
<what to do, with rationale>

## Gotchas
- ...

## Sources
- [label](url)
- ...
```

## Quality gate

- [ ] At least one primary source (not a blog post)
- [ ] Conflicts between sources surfaced, not glossed over
- [ ] If the answer updates a KB entry, propose the edit
