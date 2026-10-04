# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-10-04
### Added
- Slash commands: `/chatlog:search <phrase>`, `/chatlog:render [session] [key ...]`
  and `/chatlog:extract [session]`.
- `search.py`: case-insensitive phrase search over the index, this project by
  default or every project with `--all`; one truncated line per hit, capped.
- Scoped rendering: `--since` / `--until` (spans such as `2h` and `7d`, `today`,
  `yesterday`, clock times, ISO dates; local time), `--first N`, `--last N` and
  `--list`. A time range with no session named spans every session of the
  project. `/chatlog:render` accepts the request in plain words.
- `extract.py` and `render.py` default to the session running in the current
  directory when no session is named.

### Changed
- `render.py` no longer prints a whole session by default. Without a key it lists
  the most recent records one line each, full text is capped at about 20,000
  characters (`--max-chars`), and `--out FILE` writes a selection to a file. Its output usually lands in a conversation, and
  sessions can be very long.

## [0.1.0] - 2026-10-04
### Added
- `Stop` and `SessionEnd` hooks that append each turn's dialogue to
  `~/.claude/chatlogs/<project-slug>/<session-id>.md`, one line per message:
  `[timestamp]|USER|text` or `[timestamp]|CLAUDE|text`.
- Filtering that keeps only typed prompts and Claude's text replies. Tool calls,
  tool results, thinking, system reminders, hook output, skill bodies, compaction
  summaries and interrupt markers are left out.
- Reversible one-line escaping, so a record decodes back to exactly what was said.
- The transcript's own timestamp as each record's key, so a log line greps
  straight into the raw `.jsonl`.
- Handling for the transcript lagging behind the Stop event: a short wait, then a
  fallback to the hook payload's `last_assistant_message`, without logging the
  reply twice.
- No backfill: the first run records a cutoff, and turns that ended before it are
  never logged.
- `extract.py` to rebuild one session's log from its transcript, and `render.py`
  to turn log lines back into formatted text.
