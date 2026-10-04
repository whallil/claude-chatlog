"""The render CLI turns log records back into the text that was said."""

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, PACKAGE)
sys.path.insert(0, HERE)

import core  # noqa: E402
from test_core import ts  # noqa: E402

# A table, a tab, a literal backslash-n and a colour sequence: the formatting
# the one-line encoding has to give back intact.
REPLY = "| a | b |\n|---|---|\n| 1 | 2 |\n\n\tindented \\n stays literal\n\x1b[31mred\x1b[0m"


class RenderCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        folder = os.path.join(self.home, "chatlogs", "-home-me-proj")
        os.makedirs(folder)
        self.log_file = os.path.join(folder, "sess-1.md")
        with open(self.log_file, "w", encoding="utf-8") as handle:
            handle.write(core.format_record(ts(1), "USER", "why is the build failing?"))
            handle.write(core.format_record(ts(2), "CLAUDE", REPLY))

    def run_cli(self, *args):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.home)
        env.pop("CLAUDE_CHATLOG_DIR", None)
        return subprocess.run(
            [sys.executable, os.path.join(PACKAGE, "render.py"), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def test_whole_log_renders_as_markdown_sections(self):
        result = self.run_cli(self.log_file)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("## You", result.stdout)
        self.assertIn("## Claude", result.stdout)
        self.assertIn(ts(2), result.stdout)
        self.assertIn("| a | b |\n|---|---|\n| 1 | 2 |", result.stdout)

    def test_raw_key_lookup_returns_the_exact_original_text(self):
        result = self.run_cli(self.log_file, ts(2), "--raw")
        self.assertEqual(result.stdout, REPLY + "\n")

    def test_key_prefix_selects_matching_records(self):
        result = self.run_cli(self.log_file, ts(1)[:16], "--raw")
        self.assertEqual(result.stdout, "why is the build failing?\n")

    def test_session_id_finds_the_log(self):
        result = self.run_cli("sess-1", ts(1), "--raw")
        self.assertEqual(result.stdout, "why is the build failing?\n")

    def test_unknown_key_fails_with_a_message(self):
        result = self.run_cli(self.log_file, "1999-01-01")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("1999-01-01", result.stderr)


if __name__ == "__main__":
    unittest.main()
