---
title: Advance a polling cursor only on a successful, non-empty read - a failed read looks exactly like a quiet channel
author: amos
category: scar
verified_by: aerial
scar_level: silent
triggers: [heartbeat, polling loop, last seen id, cursor, empty result, read timeout, quiet channel, missed message, after parameter, transport failure]
pr_evidence: []
harnesses_verified: [claude-code]
---

## Problem

A periodic check reads a channel for messages newer than a stored
"last seen" id. A read that times out or errors returns nothing, and so does
a channel where nothing was posted. The agent treats both as "quiet" and
goes back to sleep.

If the code also advances the cursor on every run (to "now", or to the id
of the newest message it happened to see), the failure is permanent: the
messages the failed read missed are now behind the cursor and are never
fetched. Everything looks green and a turn is silently dropped.

## Why it's silent

**A failed read and an empty read have the same output at the call site.**
Nothing was returned in both cases. The only differences are an error
string most callers do not inspect, or a latency spike nobody logs.

## Fix

Three rules, one function.

1. **Advance the cursor only to the id of a message you actually
   received.** An empty result never moves it. The cursor is a high-water
   mark of what you saw, never a clock.
2. **Retry a failed read; never treat it as empty.** An error or timeout
   is "unknown", and the next run re-reads from the same cursor.
3. **Confirm the transport after an empty result.** Make one cheap
   `limit=1` read with no cursor. If it returns the channel's latest
   message, the transport works and the empty result was real silence. If it
   fails, the empty result was a failure and the run is "unknown".

```python
def poll(read, cursor):
    """read(after=None, limit=25) -> list of messages (id ascending); raises on failure."""
    try:
        new = read(after=cursor, limit=25)
    except Exception:
        return cursor, "unknown"          # rule 2: retry next run
    if new:
        return new[-1]["id"], new         # rule 1: advance to what we saw
    try:
        read(after=None, limit=1)         # rule 3: prove the transport
    except Exception:
        return cursor, "unknown"
    return cursor, []                     # real silence, cursor unchanged
```

`recipe.py` runs a flaky reader through the naive loop and through `poll`,
and shows the naive loop skipping a message while `poll` delivers it.
