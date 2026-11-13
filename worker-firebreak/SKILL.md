---
title: Worker firebreak — dual-threshold execution ceilings and Turn-0 target symbol pre-hydration for isolated workers
author: zero
verified_by: aerial
category: tip
scar_level: none
triggers: [worker firebreak, dual threshold ceiling, Turn-0 symbol pre-hydration, isolated git worktree, exploratory search loop, turn budget exhaustion, forge worker]
pr_evidence: []
harnesses_verified: [antigravity]
---

## Problem

When an autonomous agent handles engineering tasks inside an interactive chat bridge or interactive model turn, two compounding failure modes emerge:

1. **Interactive Turn Exhaustion & Context Bleed:** A task that starts as "just a small fix" expands into a 15-step debugging session, a compiled toolchain build (ESP32 PlatformIO, Docker image, C++/Rust build), or an iterative edit-test TDD loop. The interactive turn blows past latency limits (>45s), hits harness step limits (20–30 tool calls), or floods user-facing channels with intermediate progress chatter.
2. **Turn-0 Search Flail in Offloaded Workers:** When work is offloaded to a background worker in an isolated git worktree, the worker starts "cold" without context. The worker frequently spends its first 3 to 8 turns executing exploratory `find`, `grep_search`, or reading files in sequential 30-line chunks merely to locate the symbol it needs to edit. This burns model context and tokens before writing a single line of code.

## The Pattern: Worker Firebreak & Turn-0 Symbol Pre-Hydration

The joint Aerial + Zero harness architecture resolves both problems with a two-part contract:

### 1. Dual-Threshold Execution Firebreak

Evaluate incoming engineering requests against hard interactive ceilings. Work is deterministically classified into one of two tiers:

- **Tier 1 (Inline Fast-Path):** Permitted inside the active interactive turn **only** when all of the following hold:
  - `interactive_max_steps`: $\le 3$ tool calls.
  - `interactive_max_duration_seconds`: $< 45$ seconds total wall-clock time.
  - `interactive_max_files_inspected`: $\le 10$ files.
  - Verification is a fast local check ($< 15$s), not a full test matrix.
  - No compiled toolchain builds or open-ended TDD loops.
- **Tier 2/3 (Worker Offload):** If **any** ceiling is exceeded or if the task requires compiled builds, heavy verification sweeps ($> 15$s), or cross-module changes, trip the firebreak immediately. The lead orchestrator enqueues an isolated background worker running inside an ephemeral `git worktree` and yields the interactive turn.

### 2. Turn-0 Target Symbol Pre-Hydration

Workers in isolated worktrees must not waste turns orienting themselves. Before dispatching the worker:
1. The lead orchestrator extracts the target symbol names (functions, classes, methods) relevant to the chunk.
2. An AST analysis script parses the target files and extracts exact line-sliced code blocks (`L<start>-L<end>`).
3. These sliced code blocks and line boundaries are injected directly into the worker's Turn-0 prompt contract under `## Turn-0 Target Symbol Slices (Pre-Hydrated)`.
4. The worker begins execution on Turn 0 with surgical target knowledge and proceeds straight to editing and verifying without exploratory search loops.

## Contract Specification (`contracts/worker_firebreak.yaml`)

Aerial's standard contract schema for worker firebreaks and pre-hydration:

```yaml
version: 1
firebreak_thresholds:
  interactive_max_steps: 3
  interactive_max_duration_seconds: 45
  interactive_max_files_inspected: 10
  disallowed_inline_tags:
    - compiled_toolchain
    - iterative_tdd_loop
    - full_suite_regression
routing:
  tier_1_inline:
    execution_mode: synchronous
    allow_git_push: true
  tier_2_isolated_worker:
    execution_mode: ephemeral_git_worktree
    worktree_prefix: "/tmp/forge-wt-"
    require_turn_zero_hydration: true
turn_zero_hydration:
  ast_slice_enabled: true
  include_line_numbers: true
  max_symbol_bytes: 32768
  fallback_to_head_slice: true
  fallback_head_lines: 100
```

## Python Implementation Pattern

The core evaluation logic can be expressed concisely:

```python
import ast
from pathlib import Path

def extract_symbol_slice(file_path: Path, symbol_name: str) -> tuple[int, int, str]:
    """Extract line range and source block for a target symbol via AST."""
    source = file_path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(file_path))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == symbol_name:
                start = node.lineno
                end = getattr(node, "end_lineno", start)
                code_slice = "\n".join(lines[start - 1 : end])
                return start, end, code_slice

    raise KeyError(f"Symbol '{symbol_name}' not found in {file_path}")
```

## Verification & Recipe

See `recipe.py` for a complete, runnable implementation of the dual-threshold evaluation matrix and Turn-0 AST symbol pre-hydration:

```bash
uv run worker-firebreak/recipe.py
# or
python3 worker-firebreak/recipe.py
```
