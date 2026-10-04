#!/usr/bin/env python3
"""Turn chat-log records back into the text that was said.

The log keeps each message on one escaped line; this decodes them again. Its
output usually lands in a conversation and sessions can be very long, so it
never prints a whole session unasked and caps what it does print::

    render.py                                  # this session: list recent records
    render.py a1b2c3d4                         # that session: list recent records
    render.py a1b2c3d4 2026-10-04T13:31        # records whose key starts so, in full
    render.py --since 7d                       # this project, the last week, in full
    render.py --since today --first 10         # the first 10 records from today
    render.py --since 9 --until 10 --last 50   # between 9:00 and 10:00, at most 50
    render.py --since yesterday --list         # one line per record instead
    render.py 2026-10-04T13:31:52.306Z --raw   # this session, that text alone, exact
    render.py a1b2c3d4 --out session.md        # the whole session, to a file

``--since`` and ``--until`` take a span back from now (``90m``, ``2h``, ``7d``,
``1w``), ``today``, ``yesterday``, a clock time today (``9``, ``09:45``) or an
ISO date or date-time, all in local time. A time range with no session named
covers every session of the current project, in time order.
"""

import argparse
import glob
import os
import re
import sys
from datetime import datetime

import core

HEADINGS = {core.ROLE_USER: "You", core.ROLE_CLAUDE: "Claude"}
# A key starts "2026-10-"; a session id's fifth character is never a dash.
_KEY_LIKE_RE = re.compile(r"\d{4}-\d")


def find_log(session):
    """Resolve a log path, a session id, a unique prefix, or the current session."""
    if session is None:
        transcript = core.current_transcript()
        if transcript is None:
            sys.exit("render: no session found in this directory; name one")
        path = core.log_path(transcript)
        if not os.path.isfile(path):
            sys.exit("render: this session has no log yet (%s)" % path)
        return path
    if os.path.isfile(session):
        return session
    pattern = os.path.join(core.chatlog_root(), "*", glob.escape(session) + "*.md")
    matches = sorted(glob.glob(pattern))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.exit("render: no log matches %r (looked for %s)" % (session, pattern))
    sys.exit("render: %r matches several logs:\n  %s" % (session, "\n  ".join(matches)))


def project_logs():
    """Every log of the project this directory belongs to."""
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    folder = os.path.join(core.chatlog_root(), core.project_slug(project))
    return sorted(glob.glob(os.path.join(glob.escape(folder), "*.md")))


def read_records(paths):
    """Return ``(record, session_id)`` pairs from the logs, oldest key first."""
    found = []
    for path in paths:
        session = os.path.basename(path)[:-3]
        with open(path, encoding="utf-8") as handle:
            for record in map(core.parse_record, handle):
                if record:
                    found.append((record, session))
    if len(paths) > 1:
        found.sort(key=lambda pair: core._instant(pair[0].key))
    return found


def local_time(key):
    """Show a record key in local time; the key itself stays as logged."""
    try:
        moment = datetime.fromisoformat(key.replace("Z", "+00:00"))
    except ValueError:
        return key
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def as_markdown(record, session=None):
    heading = HEADINGS.get(record.role, record.role)
    origin = "`%s`" % record.key + (" · session `%s`" % session[:8] if session else "")
    return "## %s — %s\n%s\n\n%s\n\n" % (
        heading,
        local_time(record.key),
        origin,
        record.text,
    )


def when(option, value):
    try:
        return core.parse_when(value)
    except ValueError:
        sys.exit(
            "render: cannot read %s %r; use e.g. 2h, 7d, today, yesterday, 09:30, "
            "2026-10-04 or 2026-10-04T09:30" % (option, value)
        )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Decode chat-log records back into the text that was said."
    )
    parser.add_argument(
        "args",
        nargs="*",
        metavar="[SESSION] [KEY ...]",
        help="a session (id, unique prefix, or log path), then timestamp keys or "
        "key prefixes; the session defaults to the one running in this directory",
    )
    parser.add_argument("--since", metavar="WHEN", help="only records at or after WHEN")
    parser.add_argument("--until", metavar="WHEN", help="only records before WHEN")
    parser.add_argument("--first", type=int, metavar="N", help="keep the first N records")
    parser.add_argument("--last", type=int, metavar="N", help="keep the last N records")
    parser.add_argument(
        "--list", action="store_true", help="one truncated line per record, not full text"
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=20000,
        metavar="N",
        help="stop printing full text after about N characters (default 20000)",
    )
    parser.add_argument(
        "--raw", action="store_true", help="print only the decoded text, no headings"
    )
    parser.add_argument(
        "--out", metavar="FILE", help="write the selection as markdown to FILE, uncapped"
    )
    options = parser.parse_args(argv)
    names = list(options.args)
    session = None if not names or _KEY_LIKE_RE.match(names[0]) else names.pop(0)
    keys = tuple(names)
    since = when("--since", options.since) if options.since else None
    until = when("--until", options.until) if options.until else None
    timed = since is not None or until is not None

    # A time range with no session named means the project, not one session.
    across = timed and session is None and not keys
    pairs = read_records(project_logs() if across else [find_log(session)])
    total = len(pairs)
    if since:
        pairs = [pair for pair in pairs if core._instant(pair[0].key) >= since]
    if until:
        pairs = [pair for pair in pairs if core._instant(pair[0].key) < until]
    if keys:
        pairs = [pair for pair in pairs if pair[0].key.startswith(keys)]
        if not pairs:
            sys.exit("render: no record has a key starting with %s" % " or ".join(keys))
    scoped = timed or bool(keys) or options.first is not None
    if options.first is not None:
        pairs = pairs[: max(options.first, 0)]
    if options.last is not None:
        pairs = pairs[-options.last :] if options.last > 0 else []
    elif not scoped:
        pairs = pairs[-30:]

    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if options.out:
        if not scoped and options.last is None:
            pairs = read_records([find_log(session)])  # --out alone: the whole session
        with open(options.out, "w", encoding="utf-8", errors="backslashreplace") as out:
            out.write("".join(as_markdown(r, s if across else None) for r, s in pairs))
        print("%d records -> %s" % (len(pairs), options.out))
        return
    if not pairs:
        print("no records in that range")
        return
    if options.list or not scoped:
        if scoped:
            print("%d records" % len(pairs))
        else:
            print("%d records; showing the last %d" % (total, len(pairs)))
        for record, name in pairs:
            where = name[:8] + "  " if across else ""
            print("%s%s  %-6s %s" % (where, record.key, record.role, core.preview(record.text)))
        return
    if options.raw:
        for index, (record, _) in enumerate(pairs):
            sys.stdout.write(("\n" if index else "") + record.text + "\n")
        return
    budget = options.max_chars
    for index, (record, name) in enumerate(pairs):
        block = as_markdown(record, name if across else None)
        if len(block) > budget:
            if index == 0:  # always show something of the first record
                sys.stdout.write(block[:budget] + "…\n\n")
                index = 1
            left = len(pairs) - index
            print(
                "(%d more record(s) not shown, starting %s. Narrow the range, add "
                "--list, or use --out FILE.)" % (left, pairs[index][0].key)
                if left
                else "(record cut short at --max-chars; use --out FILE for all of it)"
            )
            return
        sys.stdout.write(block)
        budget -= len(block)


if __name__ == "__main__":
    main()
