# Contributing to chatlog

Thanks for your interest in improving chatlog! This is a small, focused project, so contributions of any size are welcome — bug reports, doc fixes, or code.

## Project layout

```
.claude-plugin/marketplace.json       # makes the repo an installable marketplace
plugins/chatlog/
  .claude-plugin/plugin.json           # plugin manifest
  hooks/hooks.json                     # registers the Stop and SessionEnd hooks
  commands/                            # /chatlog:search, /chatlog:render, /chatlog:extract
  scripts/
    core.py                            # the filter, line format, bookmark, lag handling
    hook.py                            # hook entry point: prints nothing, always exits 0
    extract.py                         # rebuild one session's log from its transcript
    render.py                          # log lines back to formatted text
    search.py                          # phrase search over the index
  tests/                               # stdlib unittest
```

`core.py` is the whole engine. Everything is intentionally **standard-library only** so it runs anywhere Python 3 does. Please keep it that way — new dependencies will be declined.

## Development setup

You need Python 3 and Claude Code. Run the tests from the repo root:

```bash
python3 -m unittest discover -s plugins/chatlog/tests
```

To exercise it as an installed plugin, point a marketplace at your local clone:

```
/plugin marketplace add /absolute/path/to/your/clone
/plugin install chatlog@claude-chatlog
```

Or load it for a single run without installing: `claude --plugin-dir plugins/chatlog`.

## Before you open a PR

- **Write the test first.** Claude Code's transcript format is internal and changes between releases, so every filter rule is pinned by a test whose fixture mirrors a real event. A new transcript shape needs a fixture before it needs code.
- **Never paste real conversation content** into fixtures or issues. Keep the event's structure and replace the text.
- **The hook must not be able to affect a session**: it prints nothing and always exits 0. Keep it that way.
- Add an entry to `CHANGELOG.md` under the "Unreleased" heading.

## Reporting bugs / requesting features

Use the issue templates. For anything security-sensitive, follow [SECURITY.md](SECURITY.md) — please don't open a public issue for vulnerabilities.

## License

By contributing, you agree that your contributions are licensed under the project's [MIT](LICENSE) license.
