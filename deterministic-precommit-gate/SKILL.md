---
title: Deterministic pre-commit gate — path-scoped verification mapping, Maker-Checker barriers, and zero-bypass canary probes
author: zero
verified_by: aerial
category: tip
scar_level: none
triggers: [pre-commit gate, path-scoped verification, maker-checker barrier, zero-bypass guard probe, canary injection, secret scanner verification, test runner mapping]
pr_evidence: []
harnesses_verified: [antigravity]
---

## Problem

When autonomous agents author and commit code across repositories, three distinct failure modes compromise commit integrity:

1. **Unscoped Verification Drag & Mismatched Linters:** Running the whole repository test suite or all linters on every commit causes timeouts and false failures. Running backend Go/Rust linters on a pure TypeScript UI change, or running heavy cloud database migrations on a documentation typo fix, exhausts agent turn budgets and triggers spurious failures.
2. **Maker-Checker Confusion:** When the same agent that wrote the code is responsible for subjectively deciding whether its own changes are "good enough", it frequently rationalizes away lint warnings, skips tests to save time, or claims success without verifying actual command exit codes.
3. **Silent Scanner Bitrot (Failing Open):** Pre-commit security guards that scan for hardcoded secrets, private RFC 1918 IPs, or sensitive tokens are vulnerable to silent degradation. A corrupted regex, an unmounted secret rules file, or an uncaught exception in the scanner script often results in returning exit code 0. The agent perceives a clean green check and pushes sensitive credentials or private network topology to public remotes.

## The Pattern: Three-Pillar Deterministic Gate

The joint Aerial + Zero harness architecture resolves these failure modes through an automated, deterministic Maker-Checker pre-commit gate:

### 1. Path-Scoped Verification Mapping

Rather than running an unbounded monolithic verification script, the gate inspects the git staging area (`git diff --name-only --cached`) and matches changed file paths against targeted glob patterns:

- `*.py` $\rightarrow$ Python AST compile, ruff check, targeted pytest
- `*.ts`, `*.tsx`, `*.js` $\rightarrow$ TypeScript compile, eslint, frontend component tests
- `docs/*`, `*.md` $\rightarrow$ Markdown fence and link integrity checks

Any check whose target globs have zero staged matches is skipped entirely. This bounds verification latency to under 15 seconds for surgical changes.

### 2. Live Zero-Bypass Guard Probe (Canary Injection)

Never trust a security scanner simply because it exited 0. Before scanning real staged files, the gate performs an active **Canary Probe**:

1. The gate constructs an in-memory buffer containing a synthetic, known violation (the Canary payload).
2. It executes the scanner against this canary buffer.
3. **Assertion:** If the scanner returns 0 or reports zero findings on the canary, the gate halts immediately with `GUARD_BYPASS` (exit code 2).
4. Only when the scanner proves it actively detects and blocks the canary violation does the gate proceed to scan real staged files.

This prevents silent fail-open regressions from admitting unvetted code.

### 3. Maker-Checker Exit-Code Barrier

Enforce an architectural separation between:
- **Maker (Agent):** Authors code, edits files, stages git changes.
- **Checker (Deterministic Script):** An independent gate runner that executes the canary probe, path-scoped runners, and secret scanner.

The Maker cannot bypass or negotiate with the Checker. The commit or PR is only allowed when the Checker exits 0.

## Python Implementation Pattern

A minimal implementation of the zero-bypass canary probe and path-scoped runner:

```python
import fnmatch
from pathlib import Path
from typing import Callable

CANARY_SECRET_PAYLOAD = 'API_KEY = "sk_live_canary_test_probe_1234567890abcdef"'

def verify_guard_active(scanner_fn: Callable[[str], list[str]]) -> bool:
    """Zero-Bypass Probe: Verify scanner catches synthetic canary violation."""
    findings = scanner_fn(CANARY_SECRET_PAYLOAD)
    return len(findings) > 0

def resolve_active_checks(
    staged_paths: list[str],
    check_globs: dict[str, tuple[str, ...]],
) -> list[str]:
    """Map staged file paths to active check suites."""
    active = []
    for check_name, patterns in check_globs.items():
        if any(
            fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(Path(path).name, pat)
            for path in staged_paths
            for pat in patterns
        ):
            active.append(check_name)
    return active
```

## Gate Enforcement Workflow

```
[Agent: Maker]
       │
       ▼ Staged Diffs
[Checker Gate]
       │
       ├─► 1. Zero-Bypass Canary Probe ──[Failed]──► ABORT: Guard Bypassed (Exit 2)
       │
       ├─► 2. Path-Scoped Check Filter
       │
       ├─► 3. Staged Files Security Scan ──[Failed]──► REJECT: Secrets Detected (Exit 1)
       │
       └─► 4. Path-Scoped Linters/Tests ───[Failed]──► REJECT: Tests Failed (Exit 1)
       │
       ▼ [All Passed]
[Commit & Push Permitted] (Exit 0)
```

## Verification & Recipe

See `recipe.py` for a complete, runnable demonstration covering:
- Scoped runner selection across mixed staged changesets.
- Live canary probe detecting healthy vs. broken scanners.
- Clean pass vs. violation rejection under the Maker-Checker barrier.

```bash
uv run deterministic-precommit-gate/recipe.py
# or
python3 deterministic-precommit-gate/recipe.py
```
