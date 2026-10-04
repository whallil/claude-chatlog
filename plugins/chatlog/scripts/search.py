#!/usr/bin/env python3
"""Search the chat-log index.

    search.py rate limit            # this project, case-insensitive
    search.py --all rate limit      # every project
    search.py --limit 50 deploy     # more hits (default 20)

Each hit is one line: session prefix, timestamp key, role, and the text around
the match. Pass the session prefix and key to ``render.py`` to read a hit in
full, or grep the key in the session's raw transcript for the surrounding
tool calls.
"""

import argparse
import glob
import os
import sys

import core

CONTEXT = 50


def snippet(text, start, length):
    """One line of text around a match."""
    lead = "…" if start > CONTEXT else ""
    begin = max(0, start - CONTEXT)
    return lead + core.preview(text[begin:], length + 2 * CONTEXT)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Search the chat-log index.")
    parser.add_argument("text", nargs="+", help="the words to find, as one phrase")
    parser.add_argument(
        "--all", action="store_true", help="search every project, not just this one"
    )
    parser.add_argument(
        "--limit", type=int, default=20, metavar="N", help="hits to show (default 20)"
    )
    options = parser.parse_args(argv)
    phrase = " ".join(options.text).lower()
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    scope = "*" if options.all else glob.escape(core.project_slug(project))
    logs = glob.glob(os.path.join(glob.escape(core.chatlog_root()), scope, "*.md"))

    hits = []
    for path in logs:
        session = os.path.basename(path)[:-3]
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if phrase not in line.lower():
                    continue  # cheap reject before decoding
                record = core.parse_record(line)
                if record is None:
                    continue
                flat = " ".join(record.text.split())
                found = flat.lower().find(phrase)
                if found >= 0:
                    hits.append((record.key, session, record.role, flat, found))
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if not hits:
        print("no match for %r in %d log(s)" % (phrase, len(logs)))
        return
    hits.sort(reverse=True)  # newest first
    for key, session, role, flat, found in hits[: options.limit]:
        print("%s  %s  %-6s %s" % (session[:8], key, role, snippet(flat, found, len(phrase))))
    if len(hits) > options.limit:
        print("(%d more; narrow the phrase or raise --limit)" % (len(hits) - options.limit))


if __name__ == "__main__":
    main()
