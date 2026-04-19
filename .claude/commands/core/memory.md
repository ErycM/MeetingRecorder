---
name: memory
description: Save or recall a durable note for this project
---

# /memory — Durable notes

> Project-local memory. Separate from the auto-memory at `/home/erycm/.claude/projects/...`.

## Usage

```bash
/memory save "Lemonade crashes under sustained load above 30 min. Workaround: restart every 25 min."
/memory list
/memory recall "lemonade"
```

## Process

- **save**: append to `.claude/sdd/architecture/PROJECT_NOTES.md` with a timestamp
- **list**: show all notes with timestamps
- **recall <keyword>**: grep the notes file for the keyword

Keep each note terse and searchable. If a note is really reference material (how Lemonade API works), put it in `.claude/kb/` instead.
