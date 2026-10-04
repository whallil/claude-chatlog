"""The plugin packaging: manifests parse, and the registered hook command
behaves the way Claude Code needs it to."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.abspath(os.path.join(PLUGIN, "..", ".."))
sys.path.insert(0, os.path.join(PLUGIN, "scripts"))
sys.path.insert(0, HERE)

import core  # noqa: E402
from test_core import assistant, text, user  # noqa: E402


def load(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as handle:
        return json.load(handle)


class ManifestTest(unittest.TestCase):
    def test_marketplace_lists_the_plugin_at_its_real_path(self):
        marketplace = load(REPO, ".claude-plugin", "marketplace.json")
        (entry,) = marketplace["plugins"]
        self.assertEqual(entry["name"], "chatlog")
        self.assertEqual(os.path.abspath(os.path.join(REPO, entry["source"])), PLUGIN)

    def test_plugin_manifest_names_the_plugin(self):
        manifest = load(PLUGIN, ".claude-plugin", "plugin.json")
        self.assertEqual(manifest["name"], "chatlog")
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")

    def test_hooks_cover_stop_and_session_end(self):
        hooks = load(PLUGIN, "hooks", "hooks.json")["hooks"]
        self.assertEqual(sorted(hooks), ["SessionEnd", "Stop"])


class HookCommandTest(unittest.TestCase):
    """Run the command from hooks.json the way Claude Code does: via a shell,
    with CLAUDE_PLUGIN_ROOT set."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        hooks = load(PLUGIN, "hooks", "hooks.json")["hooks"]
        self.command = hooks["Stop"][0]["hooks"][0]["command"]

    def run_command(self, payload, plugin_root=PLUGIN):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.home, CLAUDE_PLUGIN_ROOT=plugin_root)
        env.pop("CLAUDE_CHATLOG_DIR", None)
        return subprocess.run(
            self.command,
            shell=True,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def test_logs_a_turn_with_no_install_step(self):
        # A plugin has no installer, so there is no cutoff on the first run:
        # the hook logs the turn that just ended and records the cutoff itself.
        project = os.path.join(self.home, "projects", "-home-me-proj")
        os.makedirs(project)
        transcript = os.path.join(project, "sess-1.jsonl")
        events = [
            user("old", 1),
            assistant(2, text("old answer")),
            user("hello", 3),
            assistant(4, text("hi")),
        ]
        with open(transcript, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")
        payload = {
            "hook_event_name": "Stop",
            "session_id": "sess-1",
            "transcript_path": transcript,
            "last_assistant_message": "hi",
        }
        result = self.run_command(payload)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        root = os.path.join(self.home, "chatlogs")
        with open(os.path.join(root, "-home-me-proj", "sess-1.md")) as handle:
            roles = [core.parse_record(line)[1:] for line in handle]
        self.assertEqual(roles, [("USER", "hello"), ("CLAUDE", "hi")])
        self.assertIsNotNone(core.read_since(root))

    def test_stays_silent_when_the_plugin_files_are_gone(self):
        # python3 exits 2 for a missing script, and exit code 2 from a Stop
        # hook blocks Claude from stopping; the command must absorb that.
        result = self.run_command({"hook_event_name": "Stop"}, plugin_root="/nonexistent")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_survives_a_plugin_path_with_spaces(self):
        spaced = os.path.join(self.home, "my plugins", "chatlog")
        os.makedirs(os.path.dirname(spaced))
        os.symlink(PLUGIN, spaced)
        result = self.run_command({"hook_event_name": "Stop"}, plugin_root=spaced)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertFalse(
            os.path.exists(os.path.join(self.home, "chatlogs", ".state", "errors.log"))
        )


if __name__ == "__main__":
    unittest.main()


class SkillTest(unittest.TestCase):
    """The skill is how Claude learns the index exists and how to query it."""

    def setUp(self):
        path = os.path.join(PLUGIN, "skills", "chat-history", "SKILL.md")
        with open(path, encoding="utf-8") as handle:
            self.body = handle.read()

    def test_frontmatter_names_and_describes_the_skill(self):
        self.assertTrue(self.body.startswith("---\nname: chat-history\n"))
        header = self.body.split("---")[1]
        self.assertIn("description:", header)
        self.assertIn("earlier", header)

    def test_it_points_at_scripts_that_exist(self):
        for name in ("search.py", "render.py"):
            with self.subTest(name=name):
                self.assertIn("scripts/" + name, self.body)
                self.assertTrue(os.path.isfile(os.path.join(PLUGIN, "scripts", name)))

    def test_it_covers_handing_the_lookup_to_a_subagent(self):
        self.assertIn("subagent", self.body.lower())
