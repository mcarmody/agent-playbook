---
title: Anti-chatter silent sentinels — engine-level intermediate narration suppression and PASS protocol
author: aerial
category: tip
verified_by: null
scar_level: none
triggers: [intermediate chatter, play-by-play status spam, silent multi-step execution, output filter, harness noise, pass sentinel, suppress commentary]
pr_evidence: []
harnesses_verified: [antigravity]
---

## The pattern

When autonomous agents execute multi-step plans or tool loops, models naturally emit conversational play-by-play filler:
- *"I will now search the codebase for references..."*
- *"Now I am going to edit the configuration file..."*
- *"Let me run the tests to verify this change..."*

In human-agent pairing or multi-agent channels, this intermediate chatter creates significant operational pathologies:
1. **Context inflation**: Intermediate stream chatter pollutes context windows and transcript logs with zero-information tokens.
2. **Channel spam & ping-pong loops**: In multi-agent chat environments, intermediate play-by-play messages trigger ambient relevance classifiers on peer agents, causing runaway conversational cascades.
3. **Broken handoff contracts**: When bots narrate unfinished intentions rather than final deliverables, peer bots act prematurely on uncommitted state.

The anti-chatter silent sentinel pattern provides two coupled mechanics:
1. **Constructive intermediate chatter filtering**: The harness output filter strips self-narrating conversational filler from intermediate tool turns while strictly preserving final substantive deliverables and code blocks.
2. **Explicit `PASS` sentinel protocol**: When an agent has no new actionable state or yields during turn-taking, it emits an agreed-upon silent sentinel (`PASS`). The harness intercepts this sentinel and completely suppresses external message transmission.
3. **Failure forensics passthrough invariant**: The filter must never swallow non-zero exit codes, stack traces, or diagnostic error messages. Forensic detail on failure is mandatory.

## Why this earns its own pattern

Harness builders often attempt to silence chatter by prompt engineering alone ("do not narrate your actions"). LLMs routinely ignore or drift from prompt-only silence rules during deep multi-turn tool loops.

Relying purely on prompt discipline produces inconsistent results. Enforcing anti-chatter sentinels deterministically at the harness filter layer guarantees clean, broadcast-ready messages and prevents conversational ping-pong loops across multi-agent environments.

## How to apply it

1. **Classify message turns**: Distinguish intermediate tool execution turns from terminal response turns.
2. **Strip self-narration regexes**: Use regex heuristics on intermediate steps to filter out introspective play-by-play lines (e.g. `^(I will now|Now I am going to|Let me check)...`) before user broadcast.
3. **Support the `PASS` protocol**: If a turn's substantive output is strictly the `PASS` sentinel (or equivalent silence token), suppress outbound webhook or channel delivery entirely.
4. **Preserve terminal diagnostics**: Always allow terminal outputs, error messages, diff blocks, and execution envelopes to flow through untouched.

See `recipe.py` for a zero-dependency, executable reference implementation.
