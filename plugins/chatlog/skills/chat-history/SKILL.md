---
name: chat-history
description: Use when you need context from an earlier Claude Code session, or from earlier in a very long one — what the user asked, what was decided, what you answered, when something was discussed — or when a subagent you are briefing needs that history. Searches and reads a local index of past dialogue (the user's prompts and Claude's text replies only) and shows how to follow a hit into the full raw transcript. Use it before asking the user to repeat something they may already have told a previous session.
allowed-tools: ["Bash(python3:*)"]
---

# Chat history index

The chatlog plugin keeps one file per session at
`~/.claude/chatlogs/<project-slug>/<session-id>.md`, one line per message:

```
[2026-10-04T14:25:04.964Z]|USER|the prompt, with newlines written as \n
[2026-10-04T14:25:06.556Z]|CLAUDE|the text reply
```

It holds only what the user typed and what Claude replied in text. Tool calls,
tool results and thinking are not in it; they are in the raw transcript, and
the timestamp at the start of each line is the key that finds them there.

The scripts are in this plugin's `scripts/` directory:
`${CLAUDE_PLUGIN_ROOT}/scripts/search.py` and
`${CLAUDE_PLUGIN_ROOT}/scripts/render.py`. If that path is not already filled in
above, it is two levels up from this skill's base directory
(`<base>/../../scripts`). The examples below call it `$S`:

```bash
S="${CLAUDE_PLUGIN_ROOT}/scripts"
```

Always go through these scripts with `python3`; do not read the log files
directly. Run them from the project's working directory: "this project" is
worked out from the current directory.

## 1. Find it

```bash
python3 "$S/search.py" rate limit            # this project, case-insensitive phrase
python3 "$S/search.py" --all rate limit      # every project
python3 "$S/search.py" --limit 50 deploy     # default is 20 hits
```

Each hit is one line: session prefix, key, role, text around the match. Search
for a distinctive phrase, not a sentence; a phrase split across a line break in
the original will not match.

## 2. Read it

```bash
python3 "$S/render.py" <session> <key>              # that record in full
python3 "$S/render.py" <session> 2026-10-04T14:2    # a key prefix: those minutes
python3 "$S/render.py" --since yesterday --list     # what was covered, one line each
python3 "$S/render.py" --since 9 --until 10 --last 50
python3 "$S/render.py" <session> --out /tmp/s.md    # a whole session, to a file
```

`--since` / `--until` take `2h`, `7d`, `today`, `yesterday`, a clock time
(`9`, `09:45`) or an ISO date, in local time. With a time range and no session,
every session of the project is included in time order. `--first N` and
`--last N` trim the selection.

Sessions can be very long. Start with `search.py` or `--list`, then read only
the records you need. Full text stops at about 20,000 characters and says what
it left out; do not raise the cap or page through a whole session unless the
user asks for that.

## 3. Dig deeper

When you need what happened around a message (the commands run, the files
read, the reasoning), take the key into the raw transcript:

```bash
grep -n '2026-10-04T14:25:06.556Z' ~/.claude/projects/<project-slug>/<session-id>.jsonl
```

The session id is the log's file name; the search output shows its first eight
characters. That line is the event; the lines after it are the tool calls and
results that followed. Transcript lines can be very large, so read a few at a
time and extract fields rather than printing whole lines.

## Handing it to a subagent

A subagent starts without this context, so choose one of two ways:

- **Look it up yourself and pass the findings.** For a fact or a decision this
  is cheaper and more reliable: put the relevant quotes and their keys in the
  agent's brief.
- **Delegate the search** when it is broad ("everything we tried for the login
  bug across last month"). Give the agent, in its brief: the absolute path of
  the scripts directory, the working directory to run from, the exact
  `search.py` / `render.py` commands above, what to look for, and an
  instruction to return conclusions with session ids and keys, not raw dumps.

## Limits

- Anything before the plugin was installed is absent unless that session was
  rebuilt with `extract.py <session-id>`.
- Answers the user gave in question dialogs are not in the index; they are in
  the transcript.
- The logs are the user's private conversations. Quote what the task needs and
  do not copy them anywhere else.
