#!/usr/bin/env python3
"""Turn chat-log records back into the text that was said.

The log keeps each message on one escaped line; this decodes them again::

    render.py a1b2c3d4                                  # whole session, markdown
    render.py a1b2c3d4 2026-10-04T13:31                 # keys starting with this
    render.py a1b2c3d4 2026-10-04T13:31:52.306Z --raw   # that text alone, exact

The markdown pipes into any renderer (``| glow -``) for tables and colour.
"""

import argparse
import glob
import os
import sys
from datetime import datetime

import core

HEADINGS = {core.ROLE_USER: "You", core.ROLE_CLAUDE: "Claude"}


def find_log(session):
    """Resolve a log path, a session id, or a unique prefix of one."""
    if os.path.isfile(session):
        return session
    pattern = os.path.join(core.chatlog_root(), "*", glob.escape(session) + "*.md")
    matches = sorted(glob.glob(pattern))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.exit("render: no log matches %r (looked for %s)" % (session, pattern))
    sys.exit("render: %r matches several logs:\n  %s" % (session, "\n  ".join(matches)))


def local_time(key):
    """Show a record key in local time; the key itself stays as logged."""
    try:
        moment = datetime.fromisoformat(key.replace("Z", "+00:00"))
    except ValueError:
        return key
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Decode chat-log records back into the text that was said."
    )
    parser.add_argument(
        "log", help="a session id, a unique prefix of one, or the path to a log"
    )
    parser.add_argument(
        "keys",
        nargs="*",
        metavar="KEY",
        help="only records whose timestamp key starts with KEY",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="print only the decoded text, without headings",
    )
    args = parser.parse_args(argv)
    with open(find_log(args.log), encoding="utf-8") as handle:
        records = [record for record in map(core.parse_record, handle) if record]
    if args.keys:
        records = [r for r in records if r.key.startswith(tuple(args.keys))]
        if not records:
            sys.exit("render: no record has a key starting with %s" % " or ".join(args.keys))
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    for index, record in enumerate(records):
        if args.raw:
            sys.stdout.write(("\n" if index else "") + record.text + "\n")
        else:
            heading = HEADINGS.get(record.role, record.role)
            sys.stdout.write(
                "## %s — %s\n`%s`\n\n%s\n\n"
                % (heading, local_time(record.key), record.key, record.text)
            )


if __name__ == "__main__":
    main()
