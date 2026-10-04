"""Filtering, merging and encoding rules.

Every event shape here mirrors one found in real Claude Code transcripts
(CLI 2.1.215 - 2.1.287); the transcript format itself is undocumented.
"""

import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
)

import core  # noqa: E402
from core import Record  # noqa: E402

THINK = {"type": "thinking", "thinking": "private reasoning", "signature": "sig"}
TOOL = {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}


def ts(minute, second=0):
    return "2026-10-04T13:%02d:%02d.306Z" % (minute, second)


def text(value):
    return {"type": "text", "text": value}


def user(content, minute=1, origin="human", **extra):
    event = {
        "type": "user",
        "isSidechain": False,
        "timestamp": ts(minute),
        "message": {"role": "user", "content": content},
    }
    if origin:
        event["origin"] = {"kind": origin}
    event.update(extra)
    return event


def assistant(minute, *blocks, second=0, model="claude-fable-5-1", **extra):
    event = {
        "type": "assistant",
        "isSidechain": False,
        "timestamp": ts(minute, second),
        "message": {"role": "assistant", "model": model, "content": list(blocks)},
    }
    event.update(extra)
    return event


def tool_result(minute):
    block = {"type": "tool_result", "tool_use_id": "toolu_1", "content": "file.txt"}
    return user([block], minute, origin=None)


def queued(prompt, minute, mode="prompt", origin="human"):
    attachment = {"type": "queued_command", "prompt": prompt, "commandMode": mode}
    if origin:
        attachment["origin"] = {"kind": origin}
    return {"type": "attachment", "timestamp": ts(minute), "attachment": attachment}


def turn_end(minute):
    return {"type": "system", "subtype": "turn_duration", "timestamp": ts(minute)}


def distill(*events):
    distiller = core.Distiller()
    records = []
    for event in events:
        records.extend(distiller.feed(event))
    return records + distiller.flush()


def prompts(*events):
    """USER texts the events produce, after a control prompt that must survive.

    The control keeps a drop-rule test from passing just because the filter
    drops everything.
    """
    records = distill(user("control", 0), *events)
    texts = [record.text for record in records if record.role == "USER"]
    assert texts[:1] == ["control"], texts
    return texts[1:]


class EscapeTest(unittest.TestCase):
    def test_round_trip_is_lossless(self):
        samples = [
            "plain",
            "two\nlines\twith\ttabs\r\n",
            "a backslash \\ and a literal \\n that is not a newline",
            "\x1b[31mred\x1b[0m",
            "| a | b |\n|---|---|\n| 1 | 2 |",
            "nel\x85 ls\u2028 ps\u2029 ff\x0c",
            "emoji \U0001f600 and an em dash \u2014 stay readable",
            "lone \ud83d surrogate",
            "",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(core.unescape(core.escape(sample)), sample)

    def test_escaped_text_is_one_physical_line(self):
        escaped = core.escape("a\nb\rc\x0bd\x0ce\x1cf\x85g\u2028h\u2029i")
        self.assertEqual(len(escaped.splitlines()), 1)

    def test_common_escapes_stay_readable(self):
        self.assertEqual(core.escape("a\n\tb\\c\x1b[0m"), "a\\n\\tb\\\\c\\x1b[0m")
        self.assertEqual(core.escape("caf\u00e9 \u2014 \u2713"), "caf\u00e9 \u2014 \u2713")

    def test_record_line_keeps_pipes_in_text(self):
        line = core.format_record("2026-10-04T13:31:52.306Z", "CLAUDE", "| a | b |\nrow")
        self.assertEqual(line, "[2026-10-04T13:31:52.306Z]|CLAUDE|| a | b |\\nrow\n")
        self.assertEqual(
            core.parse_record(line),
            Record("2026-10-04T13:31:52.306Z", "CLAUDE", "| a | b |\nrow"),
        )

    def test_parse_rejects_lines_that_are_not_records(self):
        self.assertIsNone(core.parse_record("just some text\n"))


class AssistantTest(unittest.TestCase):
    def test_text_merges_across_tool_calls_into_one_record(self):
        records = distill(
            user("fix it", 1),
            assistant(2, THINK),
            assistant(2, text("Looking.\n\n"), second=1),
            assistant(2, TOOL, second=2),
            tool_result(3),
            assistant(4, THINK),
            assistant(4, text("Fixed."), second=1),
        )
        self.assertEqual(
            records,
            [
                Record(ts(1), "USER", "fix it"),
                Record(ts(2, 1), "CLAUDE", "Looking.\n\nFixed."),
            ],
        )

    def test_blocks_sharing_one_event_are_filtered_individually(self):
        records = distill(assistant(2, THINK, text("A"), TOOL, text("B")))
        self.assertEqual(records, [Record(ts(2), "CLAUDE", "A\n\nB")])

    def test_turn_without_text_produces_no_record(self):
        self.assertEqual(distill(assistant(2, THINK), assistant(2, TOOL, second=1)), [])

    def test_synthetic_and_api_error_messages_are_dropped(self):
        records = distill(
            assistant(2, text("real answer")),
            assistant(3, text("No response requested."), model="<synthetic>"),
            assistant(4, text("You've hit your session limit"), isApiErrorMessage=True),
        )
        self.assertEqual([record.text for record in records], ["real answer"])

    def test_sidechain_events_are_dropped(self):
        records = distill(
            assistant(2, text("main thread")),
            assistant(3, text("subagent"), isSidechain=True),
        )
        self.assertEqual([record.text for record in records], ["main thread"])

    def test_turn_end_starts_a_new_record(self):
        records = distill(
            assistant(2, text("first turn")),
            turn_end(3),
            assistant(4, text("second turn")),
        )
        self.assertEqual(
            records,
            [
                Record(ts(2), "CLAUDE", "first turn"),
                Record(ts(4), "CLAUDE", "second turn"),
            ],
        )


class UserPromptTest(unittest.TestCase):
    def test_typed_prompt_is_kept(self):
        typed = user("  fix the | pipe\nsecond line  ", 2)
        self.assertEqual(prompts(typed), ["fix the | pipe\nsecond line"])

    def test_prompt_without_origin_is_kept(self):
        headless = user("headless prompt", 2, origin=None, promptSource="sdk")
        self.assertEqual(prompts(headless), ["headless prompt"])

    def test_tool_results_are_dropped(self):
        self.assertEqual(prompts(tool_result(2)), [])

    def test_system_reminders_are_stripped(self):
        mixed = user("<system-reminder>\nsecret\n</system-reminder>\nreal prompt", 2)
        only = user("<system-reminder>only this</system-reminder>", 3, origin=None)
        self.assertEqual(prompts(mixed, only), ["real prompt"])

    def test_injected_markup_is_dropped(self):
        for content in (
            "<task-notification>\n<task-id>a1</task-id>\n</task-notification>",
            "<local-command-stdout>Set model</local-command-stdout>",
            "<bash-input>ls</bash-input>",
            "<div> is not rendering",
        ):
            with self.subTest(content=content):
                self.assertEqual(prompts(user(content, 2, origin=None)), [])

    def test_human_prompt_starting_with_a_tag_is_kept(self):
        typed = user("<div> is not rendering", 2)
        self.assertEqual(prompts(typed), ["<div> is not rendering"])

    def test_machinery_tags_are_dropped_even_with_human_origin(self):
        stdout = user("<local-command-stdout>x</local-command-stdout>", 2)
        self.assertEqual(prompts(stdout), [])

    def test_non_human_origin_is_dropped(self):
        injected = user("reads like a prompt", 2, origin="task-notification")
        self.assertEqual(prompts(injected), [])

    def test_hook_injected_lines_are_stripped(self):
        content = (
            "do the thing\n"
            "[09:29 EDT -- alice]\n"
            "UserPromptSubmit hook additional context: remember X"
        )
        self.assertEqual(prompts(user(content, 2)), ["do the thing"])

    def test_meta_injections_are_dropped(self):
        skill = user(
            [text("Base directory for this skill: /x\n\n# Skill body")],
            2,
            origin=None,
            isMeta=True,
        )
        expansion = user(
            [text("Please analyze this codebase")], 3, origin=None, isMeta=True
        )
        self.assertEqual(prompts(skill, expansion), [])

    def test_local_commands_are_dropped(self):
        caveat = user(
            "<local-command-caveat>The command below was run directly"
            "</local-command-caveat>",
            2,
            origin=None,
            isMeta=True,
        )
        command = user(
            "<command-name>/model</command-name>\n"
            "            <command-message>model</command-message>\n"
            "            <command-args></command-args>",
            2,
            origin=None,
        )
        stdout = user(
            "<local-command-stdout>Set model to Fable</local-command-stdout>",
            2,
            origin=None,
        )
        self.assertEqual(prompts(caveat, command, stdout), [])

    def test_typed_slash_command_is_logged_as_typed(self):
        with_args = user(
            "<command-message>review</command-message>\n"
            "<command-name>/review</command-name>\n"
            "<command-args>the auth module</command-args>",
            2,
        )
        bare = user(
            "<command-message>init</command-message>\n<command-name>/init</command-name>",
            3,
        )
        self.assertEqual(
            prompts(with_args, bare), ["/review the auth module", "/init"]
        )

    def test_compaction_summaries_are_dropped(self):
        summary = (
            "This session is being continued from a previous conversation "
            "that ran out of context."
        )
        flagged = user(
            summary,
            2,
            origin=None,
            isCompactSummary=True,
            isVisibleInTranscriptOnly=True,
        )
        unflagged = user(summary, 3, origin=None)
        self.assertEqual(prompts(flagged, unflagged), [])

    def test_interrupt_markers_are_dropped(self):
        events = [
            user([text(marker)], 2, origin=None)
            for marker in (
                "[Request interrupted by user]",
                "[Request interrupted by user for tool use]",
            )
        ]
        self.assertEqual(prompts(*events), [])

    def test_pasted_content_is_unwrapped_and_kept_verbatim(self):
        content = (
            '\n\n<pasted_content id="4933">\n'
            "Build a hook\n"
            "<system-reminder>quoted</system-reminder>\n"
            "[09:29 EDT -- alice]\n"
            '</pasted_content id="4933">\n'
        )
        self.assertEqual(
            prompts(user(content, 2)),
            ["Build a hook\n<system-reminder>quoted</system-reminder>\n[09:29 EDT -- alice]"],
        )

    def test_typed_text_and_paste_are_joined(self):
        content = (
            'see this:\n\n<pasted_content id="a1">\nline 1\nline 2\n'
            '</pasted_content id="a1">\n'
        )
        self.assertEqual(prompts(user(content, 2)), ["see this:\n\nline 1\nline 2"])

    def test_images_become_placeholders(self):
        content = [
            {"type": "image", "source": {"type": "base64", "data": "AAAA"}},
            text("what is this?"),
        ]
        self.assertEqual(prompts(user(content, 2)), ["[image]\nwhat is this?"])


class TurnStructureTest(unittest.TestCase):
    def test_queued_message_is_a_user_record_in_transcript_order(self):
        records = distill(
            user("start", 1),
            assistant(2, text("Working.")),
            assistant(2, TOOL, second=1),
            tool_result(3),
            queued("also do Y", 3),
            assistant(4, text("Done both.")),
        )
        self.assertEqual(
            records,
            [
                Record(ts(1), "USER", "start"),
                Record(ts(2), "CLAUDE", "Working."),
                Record(ts(3), "USER", "also do Y"),
                Record(ts(4), "CLAUDE", "Done both."),
            ],
        )

    def test_task_notification_attachment_does_not_split_the_reply(self):
        note = queued(
            "<task-notification>\n<task-id>a1</task-id>\n</task-notification>",
            3,
            mode="task-notification",
            origin=None,
        )
        records = distill(
            assistant(2, text("Started.")), note, assistant(4, text("Finished."))
        )
        self.assertEqual(records, [Record(ts(2), "CLAUDE", "Started.\n\nFinished.")])

    def test_task_notification_turn_gets_its_own_claude_record(self):
        note = user(
            "<task-notification>\n<task-id>a1</task-id>\n</task-notification>",
            3,
            origin="task-notification",
        )
        records = distill(
            assistant(2, text("Kicked off.")), note, assistant(4, text("It finished."))
        )
        self.assertEqual(
            records,
            [
                Record(ts(2), "CLAUDE", "Kicked off."),
                Record(ts(4), "CLAUDE", "It finished."),
            ],
        )

    def test_mid_turn_injections_do_not_split_the_reply(self):
        skill = user(
            [text("Base directory for this skill: /x")], 3, origin=None, isMeta=True
        )
        compact = user(
            "This session is being continued from a previous conversation",
            3,
            origin=None,
            isCompactSummary=True,
        )
        records = distill(
            assistant(2, text("Before.")),
            tool_result(3),
            skill,
            compact,
            assistant(4, text("After.")),
        )
        self.assertEqual(records, [Record(ts(2), "CLAUDE", "Before.\n\nAfter.")])

    def test_interrupt_ends_the_reply(self):
        interrupt = user([text("[Request interrupted by user]")], 3, origin=None)
        records = distill(
            user("a", 1),
            assistant(2, text("partial")),
            interrupt,
            user("b", 4),
            assistant(5, text("answer")),
        )
        self.assertEqual(
            [(record.role, record.text) for record in records],
            [("USER", "a"), ("CLAUDE", "partial"), ("USER", "b"), ("CLAUDE", "answer")],
        )

    def test_event_without_timestamp_gets_a_generated_key(self):
        event = user("no stamp", 1)
        del event["timestamp"]
        (record,) = distill(event)
        self.assertRegex(record.key, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

    def test_unknown_events_are_ignored(self):
        events = [{"type": "mode"}, {"type": "ai-title", "title": "x"}, {"junk": 1}]
        self.assertEqual(distill(*events), [])


if __name__ == "__main__":
    unittest.main()
