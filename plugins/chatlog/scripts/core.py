"""Shared logic for the Claude Code chat-log hook and its CLIs.

Distils a session transcript (``~/.claude/projects/<slug>/<session-id>.jsonl``)
into a searchable index of the dialogue alone, one line per message::

    [timestamp]|USER|text
    [timestamp]|CLAUDE|text

The timestamp is copied verbatim from the transcript event, so it is both the
record's key and a ``grep`` target in the raw ``.jsonl``. The text is escaped
to fit one physical line and decodes back to exactly what was said.

The transcript format is internal to Claude Code and undocumented. Every rule
here was derived from real transcripts (CLI 2.1.215 - 2.1.287) and is pinned
by ``tests/``; when a release changes the format, the tests are where to look.
"""

import hashlib
import json
import os
import re
import time
from collections import namedtuple
from datetime import datetime, timezone

try:
    import fcntl
except ImportError:  # Windows has no advisory locks; run unlocked there
    fcntl = None

ROLE_USER = "USER"
ROLE_CLAUDE = "CLAUDE"

# How long a Stop run waits for the transcript to catch up with the reply it
# was told about before logging that reply from the hook payload instead.
# Measured lag on CLI 2.1.287 is 30-80 ms.
WAIT_BUDGET = 0.75
POLL_INTERVAL = 0.015
LOCK_BUDGET = 2.0

Record = namedtuple("Record", "key role text")

# --------------------------------------------------------------------------
# Record encoding
# --------------------------------------------------------------------------

_SHORT_ESCAPES = {"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_SHORT_UNESCAPES = {"\\": "\\", "n": "\n", "r": "\r", "t": "\t"}
# Backslash, C0/C1 controls, DEL, the Unicode line and paragraph separators,
# and lone surrogates: everything that could break a line or fail to encode.
_ESCAPE_RE = re.compile(r"[\\\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]")
_UNESCAPE_RE = re.compile(r"\\(?:([\\nrt])|x([0-9a-f]{2})|u([0-9a-f]{4}))")
_RECORD_RE = re.compile(r"\[([^\]|]+)\]\|([A-Z]+)\|(.*)")
_KEY_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)")
_CANONICAL_KEY_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")


def _escape_char(match):
    char = match.group()
    short = _SHORT_ESCAPES.get(char)
    if short:
        return short
    code = ord(char)
    return "\\x%02x" % code if code < 0x100 else "\\u%04x" % code


def escape(text):
    """Encode ``text`` onto one physical line, reversibly.

    Newlines, tabs and backslashes become ``\\n``, ``\\t`` and ``\\\\``; other
    control characters (the ESC of a colour sequence, say) become ``\\xHH``.
    Printable text, including non-ASCII, is left readable.

    :param text: The message text.
    :returns: The escaped text; :func:`unescape` restores it exactly.
    """
    return _ESCAPE_RE.sub(_escape_char, text)


def _unescape_char(match):
    short, hex2, hex4 = match.groups()
    if short:
        return _SHORT_UNESCAPES[short]
    return chr(int(hex2 or hex4, 16))


def unescape(text):
    """Invert :func:`escape`."""
    return _UNESCAPE_RE.sub(_unescape_char, text)


def format_record(key, role, text):
    """Render one log line, newline included."""
    return "[%s]|%s|%s\n" % (key, role, escape(text))


def parse_record(line):
    """Parse one log line.

    The text may itself contain ``|`` (a markdown table), so only the first two
    separators are structural.

    :returns: A :class:`Record` with the text decoded, or None if ``line`` is
        not a record.
    """
    match = _RECORD_RE.fullmatch(line.rstrip("\n"))
    if not match:
        return None
    return Record(match.group(1), match.group(2), unescape(match.group(3)))


def _canonical(moment):
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (moment.microsecond // 1000)


def now_key():
    """Return the current instant in the transcript's own timestamp format."""
    return _canonical(datetime.now(timezone.utc))


def _record_key(event):
    stamp = event.get("timestamp")
    if isinstance(stamp, str) and _KEY_RE.fullmatch(stamp):
        return stamp
    return now_key()


def _instant(stamp):
    """Normalise a timestamp so that instants compare as plain strings."""
    if not isinstance(stamp, str):
        return ""
    if _CANONICAL_KEY_RE.fullmatch(stamp):
        return stamp
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return _canonical(moment)


# --------------------------------------------------------------------------
# Prompt filtering
# --------------------------------------------------------------------------

_COMPACT_PREFIX = "This session is being continued from a previous conversation"
_PLACEHOLDERS = {"image": "[image]", "document": "[document]"}
# Tags Claude Code wraps its own traffic in. A message that opens with one is
# never a typed prompt, whatever its origin says.
_MACHINERY_TAGS = frozenset(
    {
        "bash-input",
        "bash-stderr",
        "bash-stdout",
        "command-args",
        "command-contents",
        "command-message",
        "command-name",
        "local-command-caveat",
        "local-command-stderr",
        "local-command-stdout",
        "system-reminder",
        "task-notification",
        "user-prompt-submit-hook",
    }
)
_TURN_END_SUBTYPES = frozenset({"turn_duration", "stop_hook_summary"})
_NOT_A_TURN = object()

# The closing tag repeats the id attribute; that is how Claude Code writes it.
_PASTED_RE = re.compile(
    r'<pasted_content id="([^"]*)">\n?(.*?)\n?</pasted_content(?: id="\1")?>', re.S
)
_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>[ \t]*\n?", re.S)
_STAMP_LINE_RE = re.compile(
    r"^[ \t]*\[\d{1,2}:\d{2}(?::\d{2})? [A-Z]{2,5} -- [^\]\n]*\][ \t]*$\n?", re.M
)
_HOOK_LINE_RE = re.compile(
    r"^[ \t]*UserPromptSubmit hook (?:success|additional context)\b[^\n]*\n?", re.M
)
_COMMAND_RE = re.compile(r"\s*<command-(?:name|message)>")
_COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_TAG_RE = re.compile(r"<[/!?]?([A-Za-z][\w:.-]*)")
_INTERRUPT_RE = re.compile(r"\[Request interrupted by user[^\]\n]*\]")


def _strip_injections(typed):
    typed = _REMINDER_RE.sub("", typed)
    typed = _STAMP_LINE_RE.sub("", typed)
    return _HOOK_LINE_RE.sub("", typed)


def _typed_command(text):
    name = _COMMAND_NAME_RE.search(text)
    if not name or not name.group(1).strip():
        return None
    command = name.group(1).strip()
    if not command.startswith("/"):
        command = "/" + command
    args = _COMMAND_ARGS_RE.search(text)
    typed_args = _PASTED_RE.sub(lambda paste: paste.group(2), args.group(1)) if args else ""
    return (command + " " + typed_args.strip()).strip()


def clean_prompt(text, human=False):
    """Reduce one user text to what the user typed or pasted.

    Pasted blocks are unwrapped and kept verbatim. Everything else has injected
    material removed: ``<system-reminder>`` blocks, hook stamps, hook context.

    :param text: The raw text of a user message or text block.
    :param human: True when the transcript itself marks the message as typed
        by a person (``origin.kind == "human"``). Without that proof, anything
        opening with a tag is treated as injected markup.
    :returns: The clean prompt, or None if nothing typed remains.
    """
    if not isinstance(text, str):
        return None
    if _COMMAND_RE.match(text):
        # A slash command. The markup is machinery, but "/review the auth module" is
        # exactly what was typed, so rebuild that when a person sent it.
        return _typed_command(text) if human else None
    pieces = []
    position = 0
    for paste in _PASTED_RE.finditer(text):
        pieces.append(_strip_injections(text[position : paste.start()]))
        pieces.append(paste.group(2))
        position = paste.end()
    pieces.append(_strip_injections(text[position:]))
    lead = pieces[0].strip()
    if lead.startswith(_COMPACT_PREFIX) or _INTERRUPT_RE.fullmatch(lead):
        return None
    tag = _TAG_RE.match(lead)
    if tag and (not human or tag.group(1) in _MACHINERY_TAGS):
        return None
    return "".join(pieces).strip() or None


def _prompt_text(content, human):
    if isinstance(content, str):
        return clean_prompt(content, human)
    if not isinstance(content, list):
        return None
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append(clean_prompt(block.get("text"), human))
        elif kind in _PLACEHOLDERS:
            parts.append(_PLACEHOLDERS[kind])
    return "\n".join(part for part in parts if part) or None


def _origin_kind(holder):
    origin = holder.get("origin")
    return origin.get("kind") if isinstance(origin, dict) else origin


def _squash(text):
    return "".join(text.split())


def _turn_content(event):
    """Return the content of a user event that starts a turn.

    Tool results, skill bodies and compaction summaries are user events too,
    but they arrive inside a turn; for those this returns ``_NOT_A_TURN``.
    """
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        content = [
            block
            for block in content
            if not (isinstance(block, dict) and block.get("type") == "tool_result")
        ]
        if not content:
            return _NOT_A_TURN
    if (
        event.get("isMeta")
        or event.get("isCompactSummary")
        or event.get("isVisibleInTranscriptOnly")
    ):
        return _NOT_A_TURN
    return content


# --------------------------------------------------------------------------
# Transcript events -> records
# --------------------------------------------------------------------------


class Distiller:
    """Reduce transcript events, fed in file order, to dialogue records.

    A ``USER`` record is one typed prompt. A ``CLAUDE`` record is every text
    block of one reply merged; it stays open across tool calls and mid-turn
    injections and closes at the next prompt or at the end of the turn.

    :param pending: Reply text already logged from a Stop payload (whitespace
        removed) that the transcript had not caught up with; it is skipped
        when it finally arrives so the reply is not logged twice.
    """

    def __init__(self, pending=""):
        self.pending = pending
        self.activity = 0
        self._key = None
        self._blocks = []
        self._recent = []

    @property
    def last_block(self):
        """The newest reply text block seen, whitespace removed."""
        return self._recent[-1] if self._recent else ""

    def feed(self, event):
        """Consume one event.

        :returns: The records it completed, oldest first.
        """
        if not isinstance(event, dict) or event.get("isSidechain"):
            return []
        kind = event.get("type")
        if kind == "assistant":
            self._assistant(event)
        elif kind == "user":
            return self._user(event)
        elif kind == "attachment":
            return self._queued(event)
        elif kind == "system" and event.get("subtype") in _TURN_END_SUBTYPES:
            self.pending = ""
            return self.flush()
        return []

    def flush(self):
        """Close the open ``CLAUDE`` record, if any, and return it."""
        if not self._blocks:
            return []
        record = Record(self._key, ROLE_CLAUDE, "\n\n".join(self._blocks))
        self._key, self._blocks = None, []
        return [record]

    def has_final(self, final_text):
        """Tell whether the latest reply already ends with ``final_text``.

        The comparison ignores whitespace and is aligned on block boundaries,
        because ``last_assistant_message`` may join several text blocks.
        """
        target = _squash(final_text)
        count = len(self._recent)
        return any(
            "".join(self._recent[start:]) == target
            for start in range(max(0, count - 8), count)
        )

    def add_final(self, final_text):
        """Log ``final_text`` now and skip it when the transcript catches up."""
        self._add_block(final_text, now_key())
        self.pending = _squash(final_text)

    def _add_block(self, text, key):
        if not self._blocks:
            self._key = key
            self._recent = []
        self._blocks.append(text.strip())
        self._recent.append(_squash(text))
        self.activity += 1

    def _assistant(self, event):
        message = event.get("message")
        if not isinstance(message, dict):
            return
        if event.get("isApiErrorMessage") or message.get("model") == "<synthetic>":
            return  # CLI-generated notices ("session limit"), not Claude's words
        content = message.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            if self.pending:
                squashed = _squash(text)
                if self.pending.startswith(squashed):
                    self.pending = self.pending[len(squashed) :]
                    continue
                self.pending = ""
            self._add_block(text, _record_key(event))

    def _user(self, event):
        content = _turn_content(event)
        if content is _NOT_A_TURN:
            return []
        records = self.flush()  # a new turn ends the reply before it
        self.pending = ""
        kind = _origin_kind(event)
        if kind in (None, "human") and event.get("promptSource") != "system":
            text = _prompt_text(content, kind == "human")
            if text:
                records.append(Record(_record_key(event), ROLE_USER, text))
                self.activity += 1
        return records

    def _queued(self, event):
        # A message typed while Claude was working is delivered mid-turn as an
        # attachment; it never becomes a user event of its own.
        attachment = event.get("attachment")
        if not isinstance(attachment, dict):
            return []
        kind = _origin_kind(attachment)
        if (
            attachment.get("type") != "queued_command"
            or attachment.get("commandMode") != "prompt"
            or attachment.get("isMeta")
            or kind not in (None, "human")
        ):
            return []
        text = _prompt_text(attachment.get("prompt"), kind == "human")
        if not text:
            return []
        self.activity += 1
        self.pending = ""
        return self.flush() + [Record(_record_key(event), ROLE_USER, text)]


# --------------------------------------------------------------------------
# Files: logs, per-session state, the install cutoff
# --------------------------------------------------------------------------


def config_dir():
    """Return Claude Code's config directory (``~/.claude`` unless relocated)."""
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def chatlog_root():
    """Return the directory logs are written under."""
    return os.environ.get("CLAUDE_CHATLOG_DIR") or os.path.join(config_dir(), "chatlogs")


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "_"


def _session_of(transcript_path):
    return os.path.splitext(os.path.basename(transcript_path))[0]


def log_path(transcript_path, session_id=None, root=None):
    """Return ``<root>/<project-slug>/<session-id>.md`` for a transcript.

    The slug is the transcript's own directory name, so a log carries the same
    project name Claude Code uses under ``projects/``.
    """
    transcript_path = os.path.abspath(os.path.expanduser(transcript_path))
    slug = os.path.basename(os.path.dirname(transcript_path))
    session = session_id or _session_of(transcript_path)
    return os.path.join(
        root or chatlog_root(), _safe_name(slug), _safe_name(session) + ".md"
    )


def _state_path(root, session_id):
    return os.path.join(root, ".state", _safe_name(session_id) + ".json")


def _load_state(path):
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _save_state(path, state):
    scratch = "%s.%d.tmp" % (path, os.getpid())
    with open(scratch, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(scratch, path)


def _since_path(root):
    return os.path.join(root or chatlog_root(), ".state", "since")


def write_since(root=None, stamp=None):
    """Record the instant logging starts from; earlier turns are never logged.

    :returns: The stamp written.
    """
    stamp = stamp or now_key()
    path = _since_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(stamp + "\n")
    return stamp


def read_since(root=None):
    """Return the stamp :func:`write_since` recorded, or None."""
    try:
        with open(_since_path(root), encoding="utf-8") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def _digest(text):
    return hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()


def _lock(handle):
    """Take the session's exclusive lock, waiting briefly for another run."""
    if fcntl is None:
        return True
    deadline = time.monotonic() + LOCK_BUDGET
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)


def _read_events(path, offset):
    """Yield ``(event, end_offset)`` for each complete line after ``offset``.

    A final line without its newline is still being written and is left for
    the next call. A line that is not JSON yields ``None`` and is skipped.
    """
    with open(path, "rb") as handle:
        handle.seek(offset)
        while True:
            line = handle.readline()
            if not line.endswith(b"\n"):
                return
            offset += len(line)
            try:
                event = json.loads(line)
            except ValueError:
                event = None
            yield event, offset


def _initial_offset(path, since):
    """Pick where logging starts for a session seen for the first time.

    Nothing is backfilled: logging starts at the turn containing the first
    event at or after ``since``, so a turn that was in flight when the hook
    was installed is logged whole and everything before it is left alone.
    A session with no such event has nothing new and starts at its end.

    :param since: The install instant, or None if it was never recorded; then
        only the current turn is logged.
    """
    cutoff = _instant(since)
    turn_start = position = 0
    for event, end in _read_events(path, 0):
        start, position = position, end
        if not isinstance(event, dict) or event.get("isSidechain"):
            continue
        if event.get("type") == "user" and _turn_content(event) is not _NOT_A_TURN:
            turn_start = start
        if cutoff and _instant(event.get("timestamp")) >= cutoff:
            return turn_start
    return position if cutoff else turn_start


def update(transcript_path, session_id=None, final_text=None, root=None, wait=WAIT_BUDGET):
    """Append the dialogue added to a transcript since the last call.

    :param transcript_path: The session's ``.jsonl`` transcript.
    :param session_id: The session id; defaults to the transcript's file name.
    :param final_text: ``last_assistant_message`` from a Stop payload. The
        transcript is written asynchronously and usually does not contain the
        final reply yet when Stop fires, so this waits up to ``wait`` seconds
        for it and otherwise logs the reply from this text.
    :param root: The chatlogs directory; defaults to :func:`chatlog_root`.
    :param wait: Seconds to wait for a lagging transcript.
    :returns: The number of records appended.
    """
    root = root or chatlog_root()
    transcript_path = os.path.abspath(os.path.expanduser(transcript_path))
    if not os.path.isfile(transcript_path):
        return 0
    session_id = session_id or _session_of(transcript_path)
    target = log_path(transcript_path, session_id, root)
    state_file = _state_path(root, session_id)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(target, "a", encoding="utf-8") as log:
        if not _lock(log):
            return 0  # another run owns this session; the next one catches up
        state = _load_state(state_file)
        if state.get("transcript") != transcript_path:
            since = read_since(root)
            state = {
                "transcript": transcript_path,
                "offset": _initial_offset(transcript_path, since),
                "pending": "",
                "final": "",
            }
            if since is None:
                write_since(root)
        # A transcript that shrank was rewritten; resume from its new end
        # rather than re-log it.
        offset = min(int(state.get("offset") or 0), os.path.getsize(transcript_path))
        final = final_text.strip() if isinstance(final_text, str) else ""
        digest = _digest(_squash(final)) if final else ""
        distiller = Distiller(state.get("pending") or "")
        records = []
        deadline = time.monotonic() + wait
        while True:
            for event, offset in _read_events(transcript_path, offset):
                records.extend(distiller.feed(event))
            if not final or distiller.has_final(final):
                break
            if digest == state.get("final") and not distiller.activity:
                break  # Stop fired again for a reply that is already logged
            if time.monotonic() >= deadline:
                distiller.add_final(final)
                break
            time.sleep(POLL_INTERVAL)
        records.extend(distiller.flush())
        if records:
            log.write("".join(format_record(*record) for record in records))
            log.flush()
        state.update(
            offset=offset,
            pending=distiller.pending,
            final=digest or state.get("final") or "",
        )
        _save_state(state_file, state)
    return len(records)


def distil(transcript_path):
    """Distil a whole transcript.

    :returns: ``(records, end_offset, last_block)``, where ``end_offset`` is
        the byte offset consumed and ``last_block`` the newest reply text block
        with whitespace removed.
    """
    distiller = Distiller()
    records = []
    offset = 0
    for event, offset in _read_events(transcript_path, 0):
        records.extend(distiller.feed(event))
    records.extend(distiller.flush())
    return records, offset, distiller.last_block


def rebuild(transcript_path, session_id=None, root=None, out=None):
    """Re-extract one whole session, ignoring the no-backfill cutoff.

    Without ``out`` this replaces the session's log and moves the hook's
    bookmark to the end of the transcript, so a session that is still live
    carries on from there instead of logging everything a second time.

    :param out: Write here instead, leaving the session's log and state alone.
    :returns: ``(path_written, record_count)``
    """
    transcript_path = os.path.abspath(os.path.expanduser(transcript_path))
    if out:
        records, _, _ = distil(transcript_path)
        with open(out, "w", encoding="utf-8") as handle:
            handle.write("".join(format_record(*record) for record in records))
        return out, len(records)
    root = root or chatlog_root()
    session_id = session_id or _session_of(transcript_path)
    target = log_path(transcript_path, session_id, root)
    state_file = _state_path(root, session_id)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(target, "a", encoding="utf-8") as log:
        if not _lock(log):
            raise RuntimeError("log is busy, try again: %s" % target)
        records, offset, last_block = distil(transcript_path)
        log.truncate(0)
        log.write("".join(format_record(*record) for record in records))
        log.flush()
        state = {
            "transcript": transcript_path,
            "offset": offset,
            "pending": "",
            "final": _digest(last_block) if last_block else "",
        }
        _save_state(state_file, state)
    return target, len(records)
