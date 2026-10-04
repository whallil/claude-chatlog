---
description: Search the chat-log index for a phrase (this project; add --all for every project)
argument-hint: "[--all] [--limit N] <phrase>"
allowed-tools: ["Bash(python3:*)"]
---

```!
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/search.py" $ARGUMENTS
```

Those are the matching chat-log records, newest first: session prefix, timestamp key, role, and the text around the match. Summarise what they show in a few lines. Do not run anything else unless the user asks to read a hit in full.
