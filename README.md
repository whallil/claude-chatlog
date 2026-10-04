# claude-chatlog

[![validate](https://github.com/whallil/claude-chatlog/actions/workflows/validate.yml/badge.svg)](https://github.com/whallil/claude-chatlog/actions/workflows/validate.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A [Claude Code](https://claude.com/claude-code) plugin that keeps a searchable
index of every session's dialogue: only the prompts you submitted and the text
Claude replied with. Tool calls, tool results, thinking, system reminders, hook
output and skill bodies are all left out.

```
[2026-10-04T14:25:04.964Z]|USER|Reply with exactly these three lines and nothing else:\nFruit | Colour\napple | red\ndone
[2026-10-04T14:25:06.556Z]|CLAUDE|Fruit | Colour\napple | red\ndone
```

One message per line, written at the end of every turn to
`~/.claude/chatlogs/<project-slug>/<session-id>.md`.

> **This writes your conversations to disk in plain text.** That is the point,
> but know it before you install: the logs hold everything you typed or pasted
> and everything Claude replied, unencrypted, readable by anyone who can read
> your home directory. Nothing is sent anywhere.

## Why

Claude Code already stores each session as a `.jsonl` transcript, but a
transcript is mostly machinery: a two-line exchange can sit among dozens of
events. This index is the dialogue alone, small enough to `grep`, so that you or
a later Claude session can find what was said and then follow the timestamp back
into the full transcript when more detail is needed.

## Requirements

- Claude Code 2.1.47 or later (the hook uses `last_assistant_message`).
- Python 3.8+ on `PATH` as `python3`. Standard library only.
- Linux or macOS. On Windows it runs without file locking.

## Install

In Claude Code:

```
/plugin marketplace add whallil/claude-chatlog
/plugin install chatlog@claude-chatlog
```

There is nothing to configure. Logging starts with the turn in progress;
earlier conversations are never backfilled.

Turn it off and on with `/plugin` (or `claude plugin disable chatlog@claude-chatlog`).
Disabling stops logging and leaves existing logs alone.

## Using the index

```bash
# everything you asked in one project
grep -h '|USER|' ~/.claude/chatlogs/-home-you-myproject/*.md

# search all history; the file name of a hit is its session id
grep -rn 'rate limit' ~/.claude/chatlogs --include='*.md'

# dig deeper: the key is a line in the raw transcript; the tool calls,
# results and thinking around that message follow it
grep -n '<key>' ~/.claude/projects/<project-slug>/<session-id>.jsonl
```

### Claude uses it on its own

The plugin ships a skill, `chat-history`, so Claude knows the index exists
without being told. When it needs context from an earlier session (what you
asked, what was decided, when something came up) it searches the index, reads
only the records it needs, and follows a key into the raw transcript when it
needs the detail. The skill also tells it how to hand that lookup to a
subagent. You can prompt it directly too: "check our chat history for what we
decided about caching".

Nothing needs adding to your `CLAUDE.md`. If you want Claude to reach for it
more readily, one line is enough:

```
When you need context from an earlier session, use the chat-history skill before asking me to repeat it.
```

### Slash commands

| Command | What it does |
|---|---|
| `/chatlog:search <phrase>` | Find a phrase in this project's history (`--all` for every project). One line per hit, newest first, capped at 20 (`--limit N`). |
| `/chatlog:render [what to show]` | With nothing: list the current session's 30 most recent records, one line each. Otherwise say what you want in plain words or flags, scoped by session, key, time range or count. |
| `/chatlog:extract [session]` | Rebuild a session's log from its raw transcript. |

`session` is a session id or a unique prefix of one; leave it out to mean the
session you are in. `key` may be a prefix too (`2026-10-04T14:25` selects that
minute).

`/chatlog:render` takes plain words and turns them into a scoped query:

| You type | It runs |
|---|---|
| `/chatlog:render last week` | `render.py --since 7d` |
| `/chatlog:render first 10 lines from today` | `render.py --since today --first 10` |
| `/chatlog:render between 9 and 10 but only 50 lines` | `render.py --since 9 --until 10 --last 50` |
| `/chatlog:render what did we cover yesterday` | `render.py --since yesterday --until today --list` |

Times are local: a span back from now (`90m`, `2h`, `7d`, `1w`), `today`,
`yesterday`, a clock time today (`9`, `09:45`), or an ISO date or date-time. A
time range with no session named covers every session of the current project,
in time order.

Command output lands in the conversation, and sessions can be very long, so
nothing prints a whole session unasked: full text stops after about 20,000
characters and says how many records it left out. `--list` gives one line per
record, and `--out FILE` writes the selection to a file as markdown.

The commands run three scripts in the plugin's `scripts/` directory
(`search.py`, `render.py`, `extract.py`), which also work from a shell.

## The line format

```
[key]|ROLE|text
```

| Field | Meaning |
|---|---|
| `key` | The message's timestamp, copied verbatim from the transcript: UTC, millisecond precision. It is the record's unique key and an exact `grep` target in the raw `.jsonl`. |
| `ROLE` | `USER` or `CLAUDE`. |
| `text` | The message, escaped onto one physical line. |

Only the first two `|` are structural, so tables keep their pipes:
`key, role, text = line.rstrip("\n").split("|", 2)`.

The escaping is reversible; decoding gives back exactly what was said.

| In the message | In the log |
|---|---|
| newline, tab, carriage return | `\n` `\t` `\r` |
| backslash | `\\` |
| other control characters (the ESC of a colour sequence) | `\xHH` |
| Unicode line/paragraph separators, lone surrogates | `\uHHHH` |
| everything else, including non-ASCII | unchanged |

Two properties of the key worth knowing:

- **Unique within a session.** Two parallel sessions could in principle share a
  millisecond, so the globally unique pair is file name plus key.
- **Not always ascending.** Records are in the order the conversation happened.
  A message you type while Claude is working keeps the time you typed it but is
  logged where Claude received it, so its key can be earlier than the reply
  above it.

## What is kept and what is dropped

**`CLAUDE` records** hold the reply's `text` blocks and nothing else.

| Kept | Dropped |
|---|---|
| `text` blocks, merged into one record per reply (blank line between blocks) | `thinking`, `redacted_thinking`, `tool_use`, every other block type |
| | CLI-generated notices (`isApiErrorMessage`, model `<synthetic>`): "session limit", "No response requested." |
| | subagent traffic (`isSidechain`) |

A reply stays one record across tool calls, tool results, skill bodies,
compaction and background-task notifications. It ends at your next prompt
(including one sent mid-turn), at an interrupt, or at the end of the turn.

**`USER` records** hold what you typed or pasted.

| In the transcript | In the log |
|---|---|
| a typed prompt | as typed |
| a message sent while Claude was working (`queued_command` attachment) | as typed, at the point Claude received it |
| pasted text (`<pasted_content>` wrapper) | the pasted text verbatim, wrapper removed |
| a slash command you typed, e.g. `/review the auth module` | `/review the auth module` (the markup and the expanded prompt are dropped) |
| an image or document | `[image]` / `[document]` |
| `tool_result` blocks | dropped |
| `<system-reminder>…</system-reminder>` | stripped |
| hook stamps (`[09:29 EDT -- you]`) and `UserPromptSubmit hook …` lines | stripped |
| injected markup: a message opening with a tag (`<task-notification>`, `<local-command-stdout>`, `<bash-input>`…) | dropped |
| skill bodies, command expansions and other `isMeta` messages | dropped |
| built-in commands (`/model`, `/clear`) and their output | dropped |
| compaction summaries ("This session is being continued…") | dropped |
| `[Request interrupted by user…]` | dropped |
| prompts that did not come from you (task notifications, scheduled runs) | dropped |

Where the rules needed a judgment call:

- **"Starts with `<`" is not applied blindly.** Newer transcripts mark typed
  prompts with `origin.kind = "human"`. A human-marked prompt is dropped only if
  it opens with one of Claude Code's own tags, so `<div> is not rendering` is
  kept. Without that mark (older CLI versions, `claude -p`), any leading tag
  means injected markup.
- **Pastes are exempt from stripping.** Text inside `<pasted_content>` is kept
  verbatim even if it contains a `<system-reminder>` or something that looks
  like a hook stamp, because you put it there.
- **Typed slash commands are logged as typed.** A command with arguments is
  often a real question; dropping the whole message would lose it.
- **Interrupt markers are dropped**, since the log has only two roles.

## How it works

```
Stop  ─┐
       ├─► hook.py ─► core.update()
SessionEnd ┘            1. load the session's bookmark (a byte offset)
                        2. read only the complete lines added since then
                        3. Distiller: transcript events ─► records
                        4. Stop only: wait briefly for the reply to reach the
                           transcript, else take it from the hook payload
                        5. append the lines, save the bookmark
```

| Decision | Why |
|---|---|
| **Stop reads both sides from the transcript**; no `UserPromptSubmit` hook | `UserPromptSubmit` also fires for scheduled tasks and subagent reports and cannot tell them from typed prompts; the transcript can (`origin.kind`). It also blocks the model and injects its stdout into context, so a bug there would be visible. One path means the hook and the extractor share one filter. |
| **The same script on `SessionEnd`** | Stop does not fire on an interrupt. The next Stop catches up from the bookmark, but an interrupted *final* turn would never be logged without a flush at exit. |
| **Wait for the transcript, fall back to the payload** | The transcript is written asynchronously. On CLI 2.1.287 the final reply was missing when Stop fired and appeared 26-82 ms later. The hook polls for up to 0.75 s, then logs `last_assistant_message` instead and remembers it so it is not logged again when the transcript catches up. |
| **Bookmark by byte offset** | Each run seeks straight to the new tail (a 112 MB transcript costs 0.2 ms per run). Only lines ending in a newline are consumed, so a half-written line waits. |
| **A cutoff instant instead of a backfill** | The first run records `since`. A session seen for the first time starts at the turn containing the first event after that instant: old turns are never logged, and a turn in flight is logged whole. |
| **It cannot break a session** | The script prints nothing and always exits 0; failures go to `errors.log`. The hook command ends in `2>/dev/null \|\| true` because `python3` itself exits 2 when a script is missing, and exit code 2 from a Stop hook blocks Claude from stopping. |

Measured cost: 25-35 ms as a process; 64 ms and 103 ms inside real sessions,
lag wait included.

## Where things live

| What | Where |
|---|---|
| Logs | `~/.claude/chatlogs/<project-slug>/<session-id>.md` |
| Bookmarks | `~/.claude/chatlogs/.state/<session-id>.json` |
| Cutoff | `~/.claude/chatlogs/.state/since` |
| Errors | `~/.claude/chatlogs/.state/errors.log` (absent unless something failed) |

`<project-slug>` is the directory name Claude Code already uses under
`~/.claude/projects/`. Set `CLAUDE_CHATLOG_DIR` to put logs somewhere else.
The extension is `.md` though the file is a line index; `render.py` produces
actual markdown.

Headless `claude -p` runs are logged too. Runs with `--bare` or
`--setting-sources ''` load no plugins and are not.

## Backfill or repair one session

`/chatlog:extract` (or `extract.py <session-id | path.jsonl>`) runs the same filter as the hook over a
whole transcript and ignores the cutoff. By default it replaces that session's
log and moves the bookmark to the end of the transcript, so a session that is
still running carries on without duplicates. `--stdout` prints instead, and
`-o FILE` writes elsewhere and leaves the session's log alone.

## Extending

Everything lives in `plugins/chatlog/scripts/core.py`.

| To change | Edit |
|---|---|
| what counts as typed by you | `clean_prompt()`, `_MACHINERY_TAGS` |
| what counts as Claude's words | `Distiller._assistant()` |
| where a reply ends | `_TURN_END_SUBTYPES`, `_turn_content()` |
| the line format or escaping | `format_record()`, `escape()` |
| where logs go | `log_path()`, or set `CLAUDE_CHATLOG_DIR` |

The transcript format is internal to Claude Code and changes between releases,
so the rules are pinned by tests whose fixtures mirror real events. Add a
fixture for the new shape first, then run
`python3 -m unittest discover -s plugins/chatlog/tests`. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Known limits

- **Thinking is left out on purpose**, including the lead-in commentary some
  models store as a `thinking` block when a message ends in tool calls. The
  index is meant to stay low-noise, and that text is still in the raw
  transcript, one `grep` of the key away. Final replies are always text.
- **Answers given inside tool results are not logged**: what you pick or type
  in a question dialog, and feedback typed when rejecting a tool call.
- **A reply the transcript never receives** (a resume bug fixed in CLI 2.1.288)
  is still logged live from the hook payload, but the extractor cannot
  reproduce it.
- **Do not delete `.state/` under a live session**; it would be logged again
  from the cutoff. Use the extractor to rebuild a log.
- **No file locking on Windows** (`fcntl` is unavailable there).

## License

[MIT](LICENSE) © Thistle Intelligence, LLC.
