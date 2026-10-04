#!/usr/bin/env python3
"""Rebuild the clean chat log of one session from its transcript.

Uses the same filtering as the live hook, for backfilling or repairing a
single session on demand::

    extract.py                           # the session running in this directory
    extract.py a1b2c3d4                  # session id, or a unique prefix
    extract.py path/to/session.jsonl
    extract.py a1b2c3d4 --stdout         # print instead of writing
    extract.py a1b2c3d4 -o copy.md       # write somewhere else

By default the session's log under the chatlogs directory is replaced and the
hook's bookmark is moved to the end of the transcript, so a session that is
still running continues from there without duplicates.
"""

import argparse
import glob
import os
import sys

import core


def find_transcript(session):
    """Resolve a transcript path, a session id, or a unique prefix of one."""
    if session is None:
        current = core.current_transcript()
        if current is None:
            sys.exit("extract: no transcript found for a session in this directory")
        return current
    if os.path.isfile(session):
        return session
    pattern = os.path.join(
        core.config_dir(), "projects", "*", glob.escape(session) + "*.jsonl"
    )
    matches = sorted(glob.glob(pattern))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.exit("extract: no transcript matches %r (looked for %s)" % (session, pattern))
    sys.exit(
        "extract: %r matches several transcripts:\n  %s" % (session, "\n  ".join(matches))
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Rebuild the clean chat log of one Claude Code session from "
        "its transcript, with the same filtering as the live hook."
    )
    parser.add_argument(
        "session",
        nargs="?",
        help="a session id, a unique prefix of one, or the path to a .jsonl "
        "transcript (default: the session running in this directory)",
    )
    where = parser.add_mutually_exclusive_group()
    where.add_argument(
        "--stdout", action="store_true", help="print the log instead of writing it"
    )
    where.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="write to FILE and leave the session's own log untouched",
    )
    args = parser.parse_args(argv)
    transcript = find_transcript(args.session)
    if args.stdout:
        records, _, _ = core.distil(transcript)
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write("".join(core.format_record(*record) for record in records))
        return
    path, count = core.rebuild(transcript, out=args.output)
    print("%d records -> %s" % (count, path))


if __name__ == "__main__":
    main()
