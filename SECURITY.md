# Security Policy

## Supported versions

This project is pre-1.0 and ships fixes only on the latest released version. Always run the most recent `0.1.x`.

| Version       | Supported |
|---------------|-----------|
| latest 0.1.x  | ✅        |
| anything older| ❌        |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

This repo has GitHub private vulnerability reporting enabled. Go to the **Security** tab → **Report a vulnerability**, or use this link:

https://github.com/whallil/claude-chatlog/security/advisories/new

That opens a private channel between you and the maintainer. Please include reproduction steps, the affected version, and the impact you can demonstrate. This is a hobby-scale project, so expect an initial response within a few days — thanks for your patience.

## What this tool actually does (scope for reviewers)

The scripts are deliberately small and easy to audit:

- **It writes your conversations to disk in plain text.** That is its purpose. The logs under `~/.claude/chatlogs/` contain everything you typed and everything Claude replied, including anything sensitive you pasted. They are created with your normal file permissions and are not encrypted. Treat that directory like the transcripts Claude Code already keeps in `~/.claude/projects/`.
- **No network access.** Nothing is sent anywhere.
- **No shell, no `eval`, no subprocess.** The hook reads a JSON payload on stdin and a transcript file, and appends to a log file.
- **It reads only the transcript the hook payload names**, and writes only under the chatlogs directory. File names are built from the session id and the transcript's directory name, with every character outside `A-Z a-z 0-9 . _ -` replaced.
- **It cannot block or steer a session.** The hook prints nothing and always exits 0, so it has no channel back into Claude's context.

The most realistic risk is the log itself: anyone who can read your home directory can read your chat history.
