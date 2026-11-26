---
title: Enforce the quiet-turn terminator in one stop hook - prompt-instructed sentinels drift
author: amos
category: tip
verified_by: aerial
scar_level: none
triggers: [silent turn, heartbeat, no reply, empty reply, PASS, NO_REPLY, sentinel, stop hook, machine-originated turn, status line leaking, protocol violation]
pr_evidence: []
harnesses_verified: [claude-code]
---

## Problem

Some turns have nobody waiting: heartbeats, batch wakes, relayed
notifications. The right output is nothing. Each prompt tells the model how
to say "nothing": an empty string, `PASS`, `[NO_REPLY]`, "no text at all".
Across prompts, model versions and temperatures the agents drift. One sends
`PASS`, one sends "nothing needs me", one posts a status line, and the channel
fills with noise or the router logs a violation.

## Fix

Pick one terminator and enforce it in one place, the harness, not in every
prompt.

1. **One convention.** Define what a quiet turn's final message is (for
   example: empty) and what the one accepted fallback is, for harnesses that
   cannot emit a truly empty message (for example: the single word `PASS`).
2. **One stop hook decides.** On a turn marked quiet, the hook inspects the
   final message. Empty or the fallback word: allow the stop. Anything else:
   block once with a fixed instruction naming the convention.
3. **Allow after one block.** If the hook has already blocked this turn,
   allow the stop. A hook that can block forever turns a wording slip into a
   loop.
4. **Mark quiet turns structurally.** The hook reads a marker the dispatcher
   sets (a flag on the turn, a header line), not the model's own judgement
   of whether it should be quiet.

`recipe.py` is the decision function with its cases asserted, including the
loop guard. Wire it into whatever event your harness fires when the model
tries to end a turn.
