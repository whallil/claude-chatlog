## Summary

<!-- What does this change, and why? -->

## Type of change
- [ ] Bug fix
- [ ] New feature / behavior change
- [ ] Docs only
- [ ] Refactor / internal

## Testing
<!-- The filter rules are pinned by tests whose fixtures mirror real transcript events. A new transcript shape needs a fixture. -->
- [ ] `python3 -m unittest discover -s plugins/chatlog/tests` passes
- [ ] Added or updated a test for the changed behavior
- [ ] Tried it in a real Claude Code session (for hook or filter changes)

## Checklist
- [ ] The scripts stay standard-library-only
- [ ] The hook still prints nothing and always exits 0
- [ ] No real conversation content or personal paths in fixtures
- [ ] Updated `CHANGELOG.md` under "Unreleased"
