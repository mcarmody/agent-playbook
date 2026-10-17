#!/usr/bin/env python3
"""
raise_bash_timeout.py — PreToolUse hook that raises an unset Bash timeout.

The Bash tool's default timeout is 120s. A known-long command that runs past
that is SIGTERMed (exit 143) at exactly that mark, and the caller gets a
bare "command timed out" with nothing to say which step or condition was
still running. 53 such kills were logged in one fleet. A killed search is the
worst case: it reads like "no matches", so an empty result looks clean.

This hook fills in the timeout parameter the caller forgot, instead of letting
the ceiling kill the command nameless. Same fail-safe contract as the rest of
the pattern: any parse error, non-Bash tool, non-matching command, or
already-set timeout emits nothing and exits 0.

It only ever RAISES an unset timeout. It never lowers one, never touches a
timeout the caller already set (even a low one, which is explicit intent),
and never changes the command itself.

Patterns and floors:
  - test runners (pytest, npm test, npx playwright, vitest, jest): 600000ms.
    These routinely run past 120s and have no self-timeout of their own.
  - wait-for.sh carrying --timeout or --sleep above 110: the Bash timeout is
    set to that wait plus 15s, so wait-for.sh's own 124 timeout path fires
    first and names the condition that was still false.

The ceiling here is 600000 (10 minutes), inside the Bash tool's own maximum.
"""
from __future__ import annotations

import json
import re
import sys

MAX_TIMEOUT_MS = 600_000

_TEST_RUNNER = re.compile(
    r"\b(pytest|npm\s+(?:run\s+)?test|npx\s+playwright|vitest|jest)\b"
)
_WAIT_FOR = re.compile(r"\bwait-for\.sh\b")
_WAIT_FOR_ARG = re.compile(r"--(?:timeout|sleep)\s+(\d+)")


def _floor_for(command: str) -> int | None:
    if _WAIT_FOR.search(command):
        best = None
        for m in _WAIT_FOR_ARG.finditer(command):
            n = int(m.group(1))
            if n > 110 and (best is None or n > best):
                best = n
        if best is not None:
            return min((best + 15) * 1000, MAX_TIMEOUT_MS)
        return None

    if _TEST_RUNNER.search(command):
        return 600_000

    return None


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return

    if payload.get("tool_name") != "Bash":
        return

    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return

    # Only fill in an unset timeout. An explicit timeout, however low, is the
    # caller's call, not an oversight to correct.
    if tool_input.get("timeout"):
        return

    floor = _floor_for(command)
    if floor is None:
        return

    updated = dict(tool_input)
    updated["timeout"] = floor

    message = (
        f"Raised this Bash call's timeout to {floor}ms — it matched a "
        "known-long pattern (test runner / wait-for.sh long wait) and had "
        "no timeout set, so it would have hit the 120s default ceiling instead."
    )

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": updated,
            },
            "systemMessage": message,
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
