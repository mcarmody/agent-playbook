---
title: Raise the Bash tool's timeout for known-long commands, and wait without sleep
author: amos
category: tip
verified_by: null
scar_level: none
triggers: [exit 143, command timed out after 2m, killed search, sleep blocked, pytest timeout, long-running test, Bash tool timeout]
pr_evidence: []
harnesses_verified: [claude-code]
---

## Problem

The Bash tool has a default timeout of 120 seconds. A command that runs past
it is stopped with SIGTERM, and the shell reports exit 143 or
"Command timed out after 2m 0s". Three things make this expensive:

- **Test suites** routinely run longer than two minutes, and nothing inside
  them stops the run first. The whole command dies at the 120-second mark.
- **Searches over large trees** are killed partway through. A killed search
  prints nothing and looks exactly like a search that found no matches, so
  an empty result can be read as a clean one. Treat exit 143 as "the search
  did not finish", never as "nothing there".
- **Polling waits** that run for minutes die with no record of what they were
  waiting for, so the caller cannot tell which condition was still false.

The caller usually has no idea a timeout is involved, because the default is
invisible at the call site. Nothing errors on the way in; the kill is the
first signal.

## Fix

Two parts. The first is a PreToolUse hook, `raise_bash_timeout.py`, that
fills in an **unset** `timeout` for commands it recognises:

- test runners (`pytest`, `npm test`, `npx playwright`, `vitest`, `jest`):
  600000 ms (10 minutes), the Bash tool's own maximum
- `wait-for.sh` with `--timeout` or `--sleep` above 110 seconds: that wait
  plus 15 seconds, capped at 600000 ms, so the script's own timeout path
  fires first and names the condition

The hook's rules are deliberately narrow:

- It **only raises** an unset timeout. It never lowers one.
- It **never touches** a timeout the caller set, even a low one, because that
  is explicit intent.
- It **never edits the command.** It only adds a `timeout` value to the input.
- It **fails open.** Bad JSON, a non-Bash tool, a command it does not
  recognise, or any error means exit 0 and no output. The command then runs
  exactly as it would have without the hook.

The second part is `wait-for.sh`, a small wrapper for waiting on a
condition, described below.

## Install

1. Copy `raise_bash_timeout.py` into your project at `.claude/hooks/`.
2. Copy `wait-for.sh` somewhere on your PATH, or call it by full path, and
   make it executable (`chmod +x wait-for.sh`).
3. Add the hook to `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/raise_bash_timeout.py",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

The `matcher` of `Bash` limits the hook to Bash tool calls. The hook is
stdlib-only Python, so it needs no install step.

## Waiting without sleep

Some harnesses reject a foreground `sleep` outright. Reach for one of these
instead, and check first whether your harness already has a native waiting
primitive (Claude Code provides `Monitor`), since that is simpler where it
exists.

Wait for a condition. The wait ends as soon as the condition is true:

```bash
wait-for.sh "grep -q DONE /tmp/harvest.log"
wait-for.sh "test -f /tmp/out.json" --timeout 90 --interval 10
```

Wait a fixed time, only when there is genuinely nothing to poll:

```bash
wait-for.sh --sleep 300
```

Behaviour:

- Exit 0 means the condition was met.
- Exit 124 means the timeout passed first. The message names the condition
  that was still false, and prints the tail of any `--tail FILE` you gave.
- Default `--timeout` is 105 seconds, deliberately under the 120-second cap.
- A `--timeout` above 110 seconds is refused in about a second with exit 2,
  unless you also pass `--long`. Refusing up front is better than being
  killed at two minutes with no output. Pass `--long` only after you have
  raised the Bash tool's `timeout` to cover the wait. The hook does this for
  you when the command contains a long `--timeout`.

## Verify

Run the recipe from the repository root. It needs only Python 3 and a Linux
shell:

```bash
uv run raise-bash-timeout/recipe.py
```

Every line should print `PASS`, ending with `all checks passed`. Unit tests for
the hook's matching rules live beside it:

```bash
python3 -m pytest -q raise-bash-timeout
```
