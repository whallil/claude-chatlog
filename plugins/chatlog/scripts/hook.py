#!/usr/bin/env python3
"""Claude Code hook: append the dialogue of the turn that just ended to the
session's chat log.

Registered for ``Stop`` (every turn) and ``SessionEnd`` (to flush a final turn
that was interrupted: Stop does not fire on an interrupt).

A logging hook must never be able to affect a session, so this prints nothing
and always exits 0. Anything that goes wrong is recorded in
``<chatlogs>/.state/errors.log`` instead.
"""

import json
import os
import sys

ERROR_LOG_LIMIT = 256 * 1024


def _log_error():
    try:
        import time
        import traceback

        config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
        root = os.environ.get("CLAUDE_CHATLOG_DIR") or os.path.join(config, "chatlogs")
        path = os.path.join(root, ".state", "errors.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        oversized = os.path.exists(path) and os.path.getsize(path) > ERROR_LOG_LIMIT
        with open(path, "w" if oversized else "a", encoding="utf-8") as log:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            log.write("%s\n%s\n" % (stamp, traceback.format_exc()))
    except BaseException:  # noqa: BLE001 - the error log is best effort
        pass


def main():
    try:
        import core

        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("agent_id"):
            return  # only the main conversation is logged, not subagents
        transcript = payload.get("transcript_path")
        if not transcript:
            return
        final = None
        if payload.get("hook_event_name") == "Stop":
            final = payload.get("last_assistant_message")
        core.update(transcript, payload.get("session_id"), final_text=final)
    except BaseException:  # noqa: BLE001 - a logging hook never breaks a session
        _log_error()


if __name__ == "__main__":
    main()
    sys.exit(0)
