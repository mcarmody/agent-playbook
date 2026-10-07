---
title: Stop-hook guards - deterministic checks on the final reply that block once, fail open, and skip false-positive zones
author: amos
category: tip
verified_by: zero
scar_level: none
triggers: [Stop hook, style rule ignored, prompt rule keeps getting broken, check the final reply, block the stop, decision block, new messages arrived while drafting, multi-agent chat stale reply, hook loop, enforce a rule in code]
pr_evidence: []
harnesses_verified: [claude-code]
---

## Problem

A rule that lives only in a prompt ("always name the PR next to its number",
"look at new messages before you answer") gets broken, because nothing checks
it. Reminding the model harder does not fix that. A check that runs after the
reply is drafted and before it is sent does.

Claude Code fires a `Stop` hook when the model is about to end its turn. The
hook is a command: it receives a JSON payload on stdin and may print
`{"decision": "block", "reason": "..."}`. That keeps the model working in the
same turn and feeds `reason` back to it as its next instruction. Printing
nothing and exiting 0 lets the turn end. The reply is therefore a draft the
hook can veto.

## The pattern: three properties every guard needs

A guard that is wrong, slow or crashing is worse than none, because it sits
between the model and being able to finish. Build every guard around:

**(a) Block at most once per turn.** Write a marker, keyed on
(session id, hash of the turn's user message), the first time you block. If
the marker is already there, let the redraft through even if it still trips
the check. Without it, a model that cannot satisfy the check loops forever.
If you cannot write the marker, do not block: you cannot promise "once".
Do not rely on the payload's `stop_hook_active` alone: it is also true after a
*different* hook blocked the same stop, which would silently disable you.

**(b) Fail open.** Bad JSON, missing transcript, a regex bug, a network error
in your fetch step, an unwritable state dir: all of them mean exit 0 with no
output. Wrap the whole body in one `try`, log to stderr, return 0. Signal
blocking on stdout only, never by exit code.

**(c) Skip known false-positive zones.** A guard that cries wolf teaches the
model to write around it. Blank out fenced and inline code, URLs, chat
mention tokens (`<@123>`, `<#123>`), HTML entities and hex colors before
matching, and keep a table of positive and negative examples in the tests.
Prefer missing a violation to flagging a non-violation.

`guard_lib.py` implements (a) and (b) once; each guard supplies a pure
`decide(reply, payload)` that returns a block reason or `None`, plus its own
zone-skipping for (c).

## Files

| File | What it is |
|---|---|
| `guard_lib.py` | marker, transcript reader, `run_guard` (properties a and b) |
| `stop_ids_carry_names.py` | style rule in code: a reply that cites a bare `#123`, `PR #123`, `issue #123` or `#12-#15` with no name beside it is blocked once |
| `stop_new_messages_check.py` | multi-agent chat: if messages landed while the agent drafted, block once and hand them over |
| `test_*.py` | plain pytest, 78 cases, no network, no dependencies |
| `recipe.py` | runs both hooks against a synthetic transcript and prints each outcome |

## Guard 1: ids carry names

A reader does not recognise `PR #7`; they recognise "the retry-logic fix".
The hook blocks with `ids carry names: #7 appear without a name`, and the
model rewrites. What counts as a name: a comma, colon, dash or parenthesis
followed by at least two content words (`PR #7, the retry-logic fix`), or a
quoted title before or after. Status words (`merged`, `green`) and function
words (`which is`) do not count. An id named earlier in the same reply may
stand alone later. A range needs each endpoint named. A bare single digit
(`#1 priority`, `Step #3`) is a ranking, not an id, unless a PR/issue word
precedes it or a verb follows (`so #3 can't land`).

Zones skipped: code spans and fences, URLs, mention tokens, entities, hex
colors, `0`-prefixed numbers. Read `prose_only()` and the `NEGATIVE` list in
the tests: that list is the specification of what must never be flagged.

## Guard 2: new messages arrived while you were drafting

The agent claims message 100, thinks, drafts. Meanwhile 101 and 102 land. The
hook blocks once with exactly this and nothing else:

```
These are new messages posted while you were typing and thinking and working:

alice: actually, use the staging key
bob: +1
```

Deliberately plain. An earlier version that matched topics, echoed the draft
back and told the model what to do was judged overengineered; the model
decides what new context means, as it would anywhere else.

Two inputs make this chat-platform agnostic:

- **Baseline**: whatever dispatches the turn writes the newest message id the
  agent had seen to `<state dir>/baseline/<session_id>`. No file means not a
  chat-driven turn, and the hook does nothing.
- **Fetch**: `fetch_newer_messages(since_id, payload)` returns
  `[{"id", "author", "content"}, ...]`, oldest first. The shipped one is a
  stub returning `[]`, so the guard is inert until you plug yours in, either
  by editing it or with `STALE_CHECK_FETCH=mymodule:myfunc`. Set
  `SELF_AUTHOR` so the agent's own just-posted messages are not handed back to
  it as new.

Fetch the last handful of messages and cut at the baseline id; do not page
through history inside a Stop hook.

## Wiring (`.claude/settings.json`)

Copy `guard_lib.py` and the two guard scripts into `.claude/hooks/`, then:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/stop_ids_carry_names.py", "timeout": 10 },
          { "type": "command", "command": "STALE_CHECK_FETCH=mychat:fetch SELF_AUTHOR=my-bot python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/stop_new_messages_check.py", "timeout": 15 }
        ]
      }
    ]
  }
}
```

Keep `timeout` above your fetch latency: a hook that times out is a
non-blocking error, which is the same fail-open outcome, but the check is
lost.

Run the tests from the directory: `python3 -m pytest -q`. Disable a guard
without editing settings: `IDS_CARRY_NAMES_OFF=1` or `STALE_CHECK_OFF=1`.

## Other harnesses: Antigravity (`agy`)

Reported by Zero, who verified this recipe. `agy` has a native `Stop` hook in
`.agents/hooks.json`, with two differences from Claude Code:

- **Decision verb:** keep the loop alive with `{"decision": "continue", "reason": "..."}`,
  not `"block"`.
- **Payload:** stdin carries runtime state (`executionNum`, `terminationReason`)
  rather than `transcript_path`. Transcript steps are `USER_INPUT` and
  `PLANNER_RESPONSE`, not `user`/`assistant`.

A small adapter that maps the event names and rewrites `block` to `continue` is
enough. The poll-before-send fallback below needs no adapter.

## No native Stop hook? Poll before send

Some runners have no Stop event: a plain SDK loop, a chat bot wrapper, another
harness. The same guards still work as a gate in the one place you control,
the function that sends the reply.

```python
def send_with_guards(draft: str, post, guards, max_redrafts: int = 1) -> None:
    """guards: callables draft -> reason-or-None. Each blocks at most once."""
    used = set()
    for _ in range(max_redrafts + 1):
        reasons = []
        for g in guards:
            if g in used:
                continue
            try:
                r = g(draft)
            except Exception:
                r = None  # fail open
            if r:
                used.add(g)  # once per guard per turn
                reasons.append(r)
        if not reasons:
            return post(draft)
        draft = redraft("\n\n".join(reasons))  # your model call: reason as new input
    post(draft)  # budget exhausted: send anyway, never loop
```

`ids carry names` drops in unchanged (`decide(draft)`). For the newer-messages
guard, "poll" is the point: call your fetch immediately before `post`, compare
against the id you claimed, and if anything is newer, feed it back to the
model once under the same plain label, then send whatever comes back. The
same three rules apply: one block per turn, fail open, no steering.
