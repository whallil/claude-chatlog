---
description: Show chat-log records, scoped by session, time range or count (e.g. "last week", "first 10 from today", "between 9 and 10, 50 max")
argument-hint: "[what to show, in plain words or as flags]"
allowed-tools: ["Bash(python3:*)"]
---

The user wants to read part of the chat log: `$ARGUMENTS`

Translate that into one call of the render script and run it with Bash:

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render.py" [SESSION] [KEY ...] [flags]

- `SESSION`: a session id or unique prefix. Leave it out for the current session,
  or, when a time range is given, for every session of this project.
- `KEY`: a timestamp key or prefix (`2026-10-04T14:25`) to show those records.
- `--since WHEN` / `--until WHEN`: a span back from now (`90m`, `2h`, `7d`, `1w`),
  `today`, `yesterday`, a clock time today (`9`, `09:45`), or an ISO date or
  date-time. Local time. "last week" is `--since 7d`; "between 9 and 10" is
  `--since 9 --until 10`.
- `--first N` / `--last N`: keep only the first or last N records of the selection.
  "lines" in the user's request means records.
- `--list`: one truncated line per record. Use it when the user asks what is
  there, not to read it.
- `--out FILE`: write the selection to a file. Use it for a whole session.

With no arguments at all, run it bare: it lists the current session's 30 most
recent records.

Output is capped at about 20,000 characters and says what it left out. Do not
raise `--max-chars` or loop to fetch the rest unless the user asks; sessions can
be very long. Then relay what the records show, briefly.
