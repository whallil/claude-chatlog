---
description: Rebuild a session's chat log from its raw transcript (this session if none is named)
argument-hint: "[session-id | path.jsonl]"
allowed-tools: ["Bash(python3:*)"]
---

```!
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/extract.py" $ARGUMENTS
```

That rebuilt the chat log from the transcript. Report the record count and path in one line.
