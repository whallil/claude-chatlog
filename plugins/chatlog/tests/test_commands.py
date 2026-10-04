"""The scripts behind the slash commands: bounded output, current-session
defaults, and search. A command's output lands in the conversation, so none of
them may dump a whole session unasked."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN, "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import core  # noqa: E402
from test_core import assistant, text, ts, user  # noqa: E402


class CommandCase(unittest.TestCase):
    """A throwaway ~/.claude and a project directory to run commands from."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = os.path.join(tmp.name, "claude")
        self.cwd = os.path.join(tmp.name, "work", "my_app")
        os.makedirs(self.cwd)
        self.slug = core.project_slug(self.cwd)
        self.root = os.path.join(self.home, "chatlogs")
        self.projects = os.path.join(self.home, "projects", self.slug)
        os.makedirs(self.projects)

    def run_script(self, name, *args):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.home)
        for name_ in ("CLAUDE_CHATLOG_DIR", "CLAUDE_SESSION_ID", "CLAUDE_PROJECT_DIR"):
            env.pop(name_, None)
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, name), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=self.cwd,
            timeout=30,
        )

    def write_log(self, session, records, slug=None):
        folder = os.path.join(self.root, slug or self.slug)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, session + ".md"), "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(core.format_record(*record))

    def write_transcript(self, session, *events):
        path = os.path.join(self.projects, session + ".jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")
        return path


class SlugTest(unittest.TestCase):
    def test_slug_matches_claude_codes_project_directory_name(self):
        self.assertEqual(core.project_slug("/home/me/my_app.v2"), "-home-me-my-app-v2")


class RenderBoundsTest(CommandCase):
    def setUp(self):
        super().setUp()
        records = [(ts(n), "USER" if n % 2 else "CLAUDE", "message %d\nsecond line" % n) for n in range(40)]
        self.write_log("sess-1", records)

    def test_without_a_key_it_lists_recent_records_one_line_each(self):
        result = self.run_script("render.py", "sess-1")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertIn("40 records", lines[0])
        self.assertEqual(len(lines), 31)
        self.assertIn(ts(39), lines[-1])
        self.assertIn("message 39 second line", lines[-1])
        self.assertNotIn(ts(9), result.stdout)

    def test_listing_previews_are_truncated(self):
        self.write_log("sess-2", [(ts(1), "CLAUDE", "x" * 5000)])
        result = self.run_script("render.py", "sess-2")
        self.assertLess(len(result.stdout), 400)

    def test_no_session_means_the_current_one(self):
        self.write_transcript("sess-1", user("hello", 1))
        result = self.run_script("render.py", ts(39))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("message 39\nsecond line", result.stdout)

    def test_whole_session_goes_to_a_file_not_the_conversation(self):
        target = os.path.join(self.cwd, "out.md")
        result = self.run_script("render.py", "sess-1", "--out", target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(len(result.stdout), 300)
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read().count("## "), 40)


class ExtractCurrentTest(CommandCase):
    def test_no_argument_rebuilds_the_current_session(self):
        self.write_transcript("older", user("old", 1))
        newest = self.write_transcript("newer", user("hello", 1), assistant(2, text("hi")))
        os.utime(newest, (2_000_000_000, 2_000_000_000))
        result = self.run_script("extract.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(self.root, self.slug, "newer.md")) as handle:
            self.assertEqual(handle.read().count("\n"), 2)


class SearchTest(CommandCase):
    def setUp(self):
        super().setUp()
        self.write_log("aaaa1111-s", [(ts(1), "USER", "fix the Rate Limit bug"), (ts(2), "CLAUDE", "done")])
        self.write_log("bbbb2222-s", [(ts(3), "CLAUDE", "the rate limit is 5 per second\nmore")])
        self.write_log("cccc3333-s", [(ts(4), "USER", "rate limit elsewhere")], slug="-other-project")

    def test_finds_matches_in_this_project_case_insensitively(self):
        result = self.run_script("search.py", "rate", "limit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("aaaa1111", result.stdout)
        self.assertIn(ts(3), result.stdout)
        self.assertNotIn("cccc3333", result.stdout)
        self.assertNotIn("done", result.stdout)

    def test_all_flag_searches_every_project(self):
        result = self.run_script("search.py", "--all", "rate limit")
        self.assertIn("cccc3333", result.stdout)

    def test_hits_are_capped_and_the_remainder_counted(self):
        self.write_log("dddd4444-s", [(ts(n), "USER", "needle %d" % n) for n in range(30)])
        result = self.run_script("search.py", "--limit", "5", "needle")
        self.assertEqual(sum("needle" in line for line in result.stdout.splitlines()), 5)
        self.assertIn("25 more", result.stdout)

    def test_no_match_says_so(self):
        result = self.run_script("search.py", "zebra")
        self.assertEqual(result.returncode, 0)
        self.assertIn("no match", result.stdout.lower())


class CommandFilesTest(unittest.TestCase):
    def test_each_command_runs_its_script_from_the_plugin_root(self):
        for name in ("extract", "render", "search"):
            with self.subTest(name=name):
                with open(os.path.join(PLUGIN, "commands", name + ".md"), encoding="utf-8") as handle:
                    body = handle.read()
                self.assertTrue(body.startswith("---\n"))
                self.assertIn('"${CLAUDE_PLUGIN_ROOT}/scripts/%s.py"' % name, body)


if __name__ == "__main__":
    unittest.main()


class WhenTest(unittest.TestCase):
    """Time expressions are local time; keys are UTC."""

    def setUp(self):
        from datetime import datetime, timedelta, timezone

        self.zone = timezone(timedelta(hours=-4))
        self.now = datetime(2026, 10, 4, 15, 30, tzinfo=self.zone)

    def when(self, text):
        return core.parse_when(text, now=self.now)

    def test_relative_spans(self):
        self.assertEqual(self.when("2h"), "2026-10-04T17:30:00.000Z")
        self.assertEqual(self.when("7d"), "2026-09-27T19:30:00.000Z")
        self.assertEqual(self.when("1w"), "2026-09-27T19:30:00.000Z")

    def test_named_days_start_at_local_midnight(self):
        self.assertEqual(self.when("today"), "2026-10-04T04:00:00.000Z")
        self.assertEqual(self.when("yesterday"), "2026-10-03T04:00:00.000Z")

    def test_clock_times_mean_today(self):
        self.assertEqual(self.when("9"), "2026-10-04T13:00:00.000Z")
        self.assertEqual(self.when("09:45"), "2026-10-04T13:45:00.000Z")

    def test_dates_and_explicit_instants(self):
        self.assertEqual(self.when("2026-10-01"), "2026-10-01T04:00:00.000Z")
        self.assertEqual(self.when("2026-10-04T13:05:00Z"), "2026-10-04T13:05:00.000Z")

    def test_nonsense_is_rejected(self):
        with self.assertRaises(ValueError):
            self.when("soonish")


class RenderScopeTest(CommandCase):
    def setUp(self):
        super().setUp()
        self.write_log("aaaa1111-s", [(ts(n), "USER", "early %d" % n) for n in range(1, 6)])
        self.write_log("bbbb2222-s", [(ts(n), "CLAUDE", "late %d\nline two" % n) for n in range(10, 16)])
        self.write_log("cccc3333-s", [(ts(12), "USER", "other project")], slug="-other-project")
        self.write_transcript("bbbb2222-s", user("x", 10))

    def utc(self, minute):
        return "2026-10-04T13:%02d:00Z" % minute

    def test_time_range_spans_this_projects_sessions_in_order(self):
        result = self.run_script("render.py", "--since", self.utc(4), "--until", self.utc(12))
        self.assertEqual(result.returncode, 0, result.stderr)
        found = [line for line in result.stdout.splitlines() if line.startswith("## ")]
        self.assertEqual(len(found), 4)  # early 4, early 5, late 10, late 11
        self.assertLess(result.stdout.index("early 5"), result.stdout.index("late 10"))
        self.assertIn("late 11\nline two", result.stdout)
        self.assertNotIn("other project", result.stdout)

    def test_first_n_limits_a_range(self):
        result = self.run_script("render.py", "--since", self.utc(1), "--first", "2")
        self.assertIn("early 2", result.stdout)
        self.assertNotIn("early 3", result.stdout)

    def test_last_n_of_a_range(self):
        result = self.run_script("render.py", "--since", self.utc(1), "--last", "1")
        self.assertIn("late 15", result.stdout)
        self.assertNotIn("late 14", result.stdout)

    def test_a_named_session_restricts_the_range(self):
        result = self.run_script("render.py", "aaaa1111", "--since", self.utc(1))
        self.assertIn("early 5", result.stdout)
        self.assertNotIn("late", result.stdout)

    def test_list_flag_gives_one_line_per_record(self):
        result = self.run_script("render.py", "--since", self.utc(10), "--list")
        self.assertNotIn("## ", result.stdout)
        self.assertIn("late 10 line two", result.stdout)

    def test_output_is_capped_and_says_what_was_left_out(self):
        self.write_log("dddd4444-s", [(ts(n), "CLAUDE", "y" * 3000) for n in range(20, 30)])
        result = self.run_script("render.py", "dddd4444", "--since", self.utc(1), "--max-chars", "7000")
        self.assertLess(len(result.stdout), 8000)
        self.assertIn("more record", result.stdout)

    def test_empty_range_says_so(self):
        result = self.run_script("render.py", "--since", self.utc(50))
        self.assertEqual(result.returncode, 0)
        self.assertIn("no records", result.stdout.lower())
