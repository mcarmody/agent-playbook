---
title: A timed-out search returns "no matches", not an error — never read an empty result as a clean one
author: amos
category: scar
verified_by: null
scar_level: silent
triggers: [grep found nothing, exit 143, SIGTERM, command timeout, verification sweep, empty search result, ripgrep, large repository, audit found nothing]
pr_evidence: []
---

## Problem

An agent runs a recursive search to verify something — "is this pattern
gone from the codebase?", "does anything still call this function?" — over
a large tree:

```bash
grep -r "old_api_call" .        # 24 GB of repo
```

The harness kills it at its command ceiling. The agent sees an empty
result, concludes the pattern is gone, and reports the sweep clean.

On 2026-08-11 six searches died this way in one session. Two of them were
the verification sweep for an explicit instruction, so that instruction was
recorded as verified while its check had never actually run.

## Why it's silent

**A killed search and a clean search look identical at the call site.** Both
produce no matching lines. The only difference is the exit code — 143
(128+SIGTERM) or 124 (GNU `timeout`) versus grep's own 1 for "no matches" —
and almost nobody checks it, because "no output" already reads as an answer.

It gets worse in the direction agents naturally push:

- **Pruning does not rescue it.** With `.git`, `node_modules`, `.next`,
  `dist` and `__pycache__` all excluded, a root-level GNU grep over that
  same tree still blew a 120-second ceiling. One repository *alone* took 99
  seconds — one slow disk from being killed too, on a day nothing looked
  different.
- **A long pause before an empty result is the tell,** and it is the exact
  moment an agent is most inclined to move on.
- **The negative result is the dangerous one.** A search that finds
  something is self-evidently alive. A search that finds nothing is
  indistinguishable from a search that never ran.

## Fix

Three parts, in order of how much they buy you.

**1. Use a search tool that finishes.** `rg` (ripgrep) respects
`.gitignore` and skips `.git` with no flags, and the difference is not
marginal — on the tree above: whole workspace 19.7s with `rg` against a
ceiling-blowing GNU grep, and for the single repo that took grep 99
seconds, `rg` took **0.66**. That is the fix that makes the other two
rarely matter.

**2. Check the exit code, always, and treat a kill as an error.** Distinguish
these three outcomes — they are not the same answer:

| exit | meaning | what to report |
|---|---|---|
| 0 | matches found | the matches |
| 1 | ran to completion, nothing matched | a real negative |
| 124 / 143 / >128 | killed before finishing | **unknown — re-run narrower** |

**3. Prove the search can find something before trusting it to find
nothing.** Run it once against a pattern you know is present (a positive
control). If the control comes back empty, your search is broken, not your
codebase clean. This is cheap and it is the only check that survives a tool
you have not profiled.

Two traps worth naming, because both have cost real time:

- `command -v rg` matches a shell function or harness shim as readily as a
  real binary, so it can report success in an interactive session and then
  `command not found` from inside a plain `#!/bin/bash` script. `type -a rg`
  distinguishes them.
- `git grep` is faster still inside a repository, but it searches **tracked
  files only**. If nested repositories under the tree are plain directories
  rather than submodules, a top-level `git grep` silently skips every one of
  them — the same clean-looking empty result, from a different cause.

`recipe.py` runs a real killed search beside a real clean one and shows
that the output is identical and the exit code is not.
