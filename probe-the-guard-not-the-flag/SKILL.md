---
title: Probe the guard, not the flag - a test that reads "guard enabled" can pass while the guard is off
author: amos
category: tip
verified_by: zero
scar_level: none
triggers: [capability test, guard test, safety check, permission check, read-only mode, sandbox, deny by default, authorization test, hook installed, feature flag says enabled, test passes but enforcement unproven, cross-tenant check]
pr_evidence: []
harnesses_verified: [claude-code]
---

## Problem

An agent or a test suite needs to show that a guard works: a hook that blocks
dangerous commands, a read-only mode, a tenant boundary, a sandbox. The cheap
check reads the setting that says the guard is on:

```python
assert os.environ["GUARD_ENABLED"] == "1"
```

That proves the setting parses. It does not prove the guard is wired into the
path the forbidden action takes. A guard can be configured, reported as on, and
still never run: the hook file is missing, the matcher names the wrong tool,
the process started before the config changed, or a second code path skips it.
The flag-reading test passes in every one of those cases.

## Pattern

Attempt the forbidden action and assert that it is refused. Treat the refusal
as the thing under test.

- **Pick an action that is safe to attempt.** Target a scratch path, a
  throwaway account, a dry-run endpoint or a command that does nothing if it
  succeeds (for example, writing a marker file in an empty temp directory).
  If the guard fails, the cost is a marker file.
- **Assert the refusal at the boundary, not the message.** A refusal is a
  non-zero exit, an HTTP 403, a blocked syscall or a hook decision of block.
  Match that signal. Do not match prose, which changes between versions.
- **Assert the side effect did not happen.** Check the marker file is absent or
  the other tenant's row was not returned. A refusal message with the effect
  still applied is a failed guard.
- **Run a positive control.** Perform an allowed action through the same path
  and assert it succeeds. If both the allowed and the forbidden action fail,
  the harness is broken, not the guard enforcing (see
  `killed-search-reads-as-no-matches`, part 3).
- **Keep a broken-guard fixture.** Run the same test against a deliberately
  unwired guard and require it to fail. A test that cannot fail on a broken
  guard is not a test.

For a multi-tenant service the same shape is a cross-tenant probe: sign in as
user A, request user B's rows, and assert an empty or refused result. "My app
does not leak" becomes a check the agent runs, not a property it assumes.

## Run it

`recipe.py` builds a tiny guard, wires it correctly, then leaves it unwired
while the flag still says on. The flag test passes both times. The probe test
passes the first time and fails the second.

```bash
uv run probe-the-guard-not-the-flag/recipe.py
python3 probe-the-guard-not-the-flag/recipe.py
```

Expected: the final line reports that the flag test could not tell the two
guards apart and the probe test could.

## Related

- `killed-search-reads-as-no-matches`: a positive control, so an empty result
  is not read as a clean one.
- `stop-hook-guards`: a guard that can be installed, so it is worth probing.
