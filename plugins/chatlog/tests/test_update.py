"""Incremental logging: state, catch-up, transcript lag, the no-backfill rule,
and the two command-line entry points (the hook and the extractor)."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, PACKAGE)
sys.path.insert(0, HERE)

import core  # noqa: E402
from test_core import TOOL, assistant, text, tool_result, ts, turn_end, user  # noqa: E402


class LogCase(unittest.TestCase):
    """A throwaway ~/.claude: one transcript, one chatlogs root."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.root = os.path.join(self.home, "chatlogs")
        project = os.path.join(self.home, "projects", "-home-me-proj")
        os.makedirs(project)
        self.transcript = os.path.join(project, "sess-1.jsonl")
        open(self.transcript, "w").close()
        self.log_file = os.path.join(self.root, "-home-me-proj", "sess-1.md")
        # Installed before every fixture event, so sessions count as new.
        core.write_since(self.root, "2026-10-04T00:00:00.000Z")

    def append(self, *events):
        with open(self.transcript, "a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")

    def update(self, final=None, wait=0):
        return core.update(
            self.transcript, "sess-1", final_text=final, root=self.root, wait=wait
        )

    def records(self):
        if not os.path.exists(self.log_file):
            return []
        with open(self.log_file, encoding="utf-8") as handle:
            return [core.parse_record(line) for line in handle]

    def logged(self):
        return [(record.role, record.text) for record in self.records()]


class UpdateTest(LogCase):
    def test_log_lands_under_project_slug_and_session_id(self):
        self.append(user("hello", 1), assistant(2, text("hi")))
        self.update()
        self.assertEqual(self.logged(), [("USER", "hello"), ("CLAUDE", "hi")])

    def test_each_run_appends_only_new_events(self):
        self.append(user("one", 1), assistant(2, text("first")))
        self.assertEqual(self.update(), 2)
        self.append(turn_end(3), user("two", 4), assistant(5, text("second")))
        self.assertEqual(self.update(), 2)
        self.assertEqual(self.update(), 0)
        self.assertEqual(
            self.logged(),
            [("USER", "one"), ("CLAUDE", "first"), ("USER", "two"), ("CLAUDE", "second")],
        )

    def test_incomplete_last_line_is_left_for_the_next_run(self):
        line = json.dumps(user("half written", 1))
        with open(self.transcript, "a") as handle:
            handle.write(line[:20])
        self.assertEqual(self.update(), 0)
        with open(self.transcript, "a") as handle:
            handle.write(line[20:] + "\n")
        self.update()
        self.assertEqual(self.logged(), [("USER", "half written")])

    def test_corrupt_line_is_skipped(self):
        with open(self.transcript, "a") as handle:
            handle.write("{not json}\n")
        self.append(user("still logged", 1))
        self.update()
        self.assertEqual(self.logged(), [("USER", "still logged")])

    def test_missing_transcript_is_a_no_op(self):
        missing = os.path.join(self.home, "projects", "-home-me-proj", "nope.jsonl")
        self.assertEqual(core.update(missing, "nope", root=self.root), 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, "-home-me-proj")))


class NoBackfillTest(LogCase):
    def test_turns_finished_before_install_are_not_logged(self):
        core.write_since(self.root, ts(10))
        self.append(
            user("old", 1),
            assistant(2, text("old answer")),
            turn_end(3),
            user("new", 11),
            assistant(12, text("new answer")),
        )
        self.update()
        self.assertEqual(self.logged(), [("USER", "new"), ("CLAUDE", "new answer")])

    def test_turn_in_flight_at_install_is_logged_whole(self):
        core.write_since(self.root, ts(10))
        self.append(
            user("old", 1),
            assistant(2, text("old answer")),
            turn_end(3),
            user("in flight", 9),
            assistant(9, TOOL, second=30),
            tool_result(11),
            assistant(12, text("done")),
        )
        self.update()
        self.assertEqual(self.logged(), [("USER", "in flight"), ("CLAUDE", "done")])

    def test_session_idle_since_install_logs_nothing_until_it_is_used(self):
        core.write_since(self.root, ts(10))
        self.append(user("old", 1), assistant(2, text("old answer")))
        self.assertEqual(self.update(), 0)
        self.append(user("later", 20), assistant(21, text("later answer")))
        self.update()
        self.assertEqual(self.logged(), [("USER", "later"), ("CLAUDE", "later answer")])


class LagTest(LogCase):
    def test_reply_comes_from_the_payload_when_the_transcript_lags(self):
        self.append(user("q", 1))
        self.update(final="answer")
        self.assertEqual(self.logged(), [("USER", "q"), ("CLAUDE", "answer")])

    def test_payload_reply_is_not_repeated_when_the_transcript_catches_up(self):
        self.append(user("q", 1))
        self.update(final="answer")
        self.append(
            assistant(2, text("answer")),
            turn_end(3),
            user("next", 4),
            assistant(5, text("more")),
        )
        self.update(final="more")
        self.assertEqual(
            self.logged(),
            [("USER", "q"), ("CLAUDE", "answer"), ("USER", "next"), ("CLAUDE", "more")],
        )

    def test_payload_reply_joins_the_text_already_in_the_transcript(self):
        self.append(
            user("q", 1),
            assistant(2, text("Working on it.")),
            assistant(2, TOOL, second=1),
            tool_result(3),
        )
        self.update(final="All done.")
        self.assertEqual(
            self.logged(), [("USER", "q"), ("CLAUDE", "Working on it.\n\nAll done.")]
        )

    def test_waits_for_a_lagging_transcript(self):
        self.append(user("q", 1))
        writer = threading.Timer(0.05, self.append, [assistant(2, text("answer"))])
        writer.start()
        self.addCleanup(writer.join)
        self.update(final="answer", wait=5)
        # The reply's key is the transcript's own timestamp, not "now".
        self.assertEqual([record.key for record in self.records()], [ts(1), ts(2)])

    def test_same_reply_is_not_logged_twice_when_stop_fires_again(self):
        self.append(user("q", 1), assistant(2, text("answer")))
        self.update(final="answer")
        self.update(final="answer")
        self.assertEqual(self.logged(), [("USER", "q"), ("CLAUDE", "answer")])


class RebuildTest(LogCase):
    def test_rebuild_matches_what_the_hook_logged_turn_by_turn(self):
        turns = [
            [
                user("one", 1),
                assistant(2, text("first")),
                assistant(2, TOOL, second=1),
                tool_result(3),
                assistant(4, text("still first")),
            ],
            [turn_end(5), user("two", 6), assistant(7, text("second"))],
            [turn_end(8), user("three", 9), assistant(10, text("third"))],
        ]
        for turn in turns:
            self.append(*turn)
            self.update()
        with open(self.log_file, encoding="utf-8") as handle:
            live = handle.read()
        rebuilt = os.path.join(self.home, "rebuilt.md")
        core.rebuild(self.transcript, "sess-1", root=self.root, out=rebuilt)
        with open(rebuilt, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), live)
        self.assertEqual(live.count("\n"), 6)

    def test_rebuild_ignores_the_install_cutoff(self):
        core.write_since(self.root, ts(30))
        self.append(user("old", 1), assistant(2, text("old answer")))
        core.rebuild(self.transcript, "sess-1", root=self.root)
        self.assertEqual(self.logged(), [("USER", "old"), ("CLAUDE", "old answer")])

    def test_hook_continues_after_a_rebuild_without_duplicates(self):
        self.append(user("one", 1), assistant(2, text("first")))
        self.update(final="first")
        core.rebuild(self.transcript, "sess-1", root=self.root)
        self.update(final="first")
        self.append(turn_end(3), user("two", 4), assistant(5, text("second")))
        self.update(final="second")
        self.assertEqual(
            self.logged(),
            [("USER", "one"), ("CLAUDE", "first"), ("USER", "two"), ("CLAUDE", "second")],
        )


class HookScriptTest(LogCase):
    def run_hook(self, stdin, root=None):
        env = dict(os.environ, CLAUDE_CHATLOG_DIR=root or self.root)
        return subprocess.run(
            [sys.executable, os.path.join(PACKAGE, "hook.py")],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def payload(self, **fields):
        base = {"session_id": "sess-1", "transcript_path": self.transcript}
        base.update(fields)
        return json.dumps(base)

    def assert_silent_success(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_stop_payload_writes_the_log(self):
        self.append(user("hello", 1), assistant(2, text("hi there")))
        stdin = self.payload(
            hook_event_name="Stop",
            last_assistant_message="hi there",
            stop_hook_active=False,
        )
        self.assert_silent_success(self.run_hook(stdin))
        self.assertEqual(self.logged(), [("USER", "hello"), ("CLAUDE", "hi there")])

    def test_session_end_payload_flushes_an_interrupted_turn(self):
        self.append(user("hello", 1), assistant(2, text("partial")))
        stdin = self.payload(hook_event_name="SessionEnd", reason="other")
        self.assert_silent_success(self.run_hook(stdin))
        self.assertEqual(self.logged(), [("USER", "hello"), ("CLAUDE", "partial")])

    def test_subagent_payload_is_ignored(self):
        self.append(user("hello", 1), assistant(2, text("hi")))
        stdin = self.payload(hook_event_name="Stop", agent_id="agent-1")
        self.assert_silent_success(self.run_hook(stdin))
        self.assertEqual(self.logged(), [])

    def test_bad_input_never_fails_or_prints(self):
        for stdin in (
            "",
            "not json",
            "[]",
            "{}",
            json.dumps({"transcript_path": "/nonexistent/x.jsonl"}),
            json.dumps({"transcript_path": 42}),
        ):
            with self.subTest(stdin=stdin):
                self.assert_silent_success(self.run_hook(stdin))

    def test_unwritable_log_root_never_fails_or_prints(self):
        self.append(user("hello", 1))
        blocked = os.path.join(self.transcript, "chatlogs")  # under a regular file
        stdin = self.payload(hook_event_name="Stop")
        self.assert_silent_success(self.run_hook(stdin, root=blocked))

    def test_internal_error_is_recorded_in_the_error_log(self):
        self.append(user("hello", 1))
        # The project's log directory cannot be created: a file is in the way.
        open(os.path.join(self.root, "-home-me-proj"), "w").close()
        stdin = self.payload(hook_event_name="Stop")
        self.assert_silent_success(self.run_hook(stdin))
        with open(os.path.join(self.root, ".state", "errors.log")) as handle:
            self.assertIn("Traceback", handle.read())


class ExtractCliTest(LogCase):
    def run_cli(self, *args):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.home, CLAUDE_CHATLOG_DIR=self.root)
        return subprocess.run(
            [sys.executable, os.path.join(PACKAGE, "extract.py"), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def test_transcript_path_writes_the_log(self):
        self.append(user("hello", 1), assistant(2, text("hi")))
        result = self.run_cli(self.transcript)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.logged(), [("USER", "hello"), ("CLAUDE", "hi")])

    def test_session_id_finds_the_transcript(self):
        self.append(user("hello", 1), assistant(2, text("hi")))
        result = self.run_cli("sess-1", "--stdout")
        self.assertEqual(
            result.stdout, "[%s]|USER|hello\n[%s]|CLAUDE|hi\n" % (ts(1), ts(2))
        )
        self.assertEqual(self.logged(), [])

    def test_output_option_writes_elsewhere_and_leaves_the_log_alone(self):
        self.append(user("hello", 1), assistant(2, text("hi")))
        target = os.path.join(self.home, "copy.md")
        result = self.run_cli("sess-1", "-o", target)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read().count("\n"), 2)
        self.assertEqual(self.logged(), [])

    def test_unknown_session_fails_with_a_message(self):
        result = self.run_cli("no-such-session")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no-such-session", result.stderr)


if __name__ == "__main__":
    unittest.main()
