# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
recipe.py — Worker firebreak with Turn-0 AST symbol pre-hydration.

Demonstrates the dual-threshold execution firebreak and Turn-0 symbol pre-hydration
pattern from the joint Aerial + Zero harness architecture:
  1. Dual-Threshold Ceiling: Evaluates whether an engineering task fits within
     an interactive turn (Tier 1 Inline Fast-Path: <=3 tool steps, <45 seconds,
     <=10 files inspected) or trips the firebreak into an isolated background
     git worktree worker (Tier 2/3 FORGE Build).
  2. Turn-0 AST Pre-Hydration: Analyzes target source files via Python's AST to
     extract exact function/class line slices and injects them directly into the
     worker's Turn-0 prompt contract, eliminating exploratory search loops.

Usage:
  uv run worker-firebreak/recipe.py
  python3 worker-firebreak/recipe.py
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import sys
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class ExecutionTier(str, Enum):
    INLINE_FASTPATH = "inline_fastpath"
    WORKER_OFFLOAD = "worker_offload"


@dataclasses.dataclass(frozen=True)
class FirebreakThresholds:
    """Hard ceilings bounding interactive model turns."""
    interactive_max_steps: int = 3
    interactive_max_duration_seconds: float = 45.0
    interactive_max_files_inspected: int = 10


@dataclasses.dataclass
class TaskProfile:
    """Engineering task metrics for firebreak evaluation."""
    name: str
    estimated_steps: int
    estimated_duration_seconds: float
    files_inspected: int
    requires_compiled_toolchain: bool = False
    is_iterative_tdd: bool = False


@dataclasses.dataclass
class FirebreakDecision:
    """Outcome of firebreak ceiling evaluation."""
    task_name: str
    tier: ExecutionTier
    reason: str
    breached_limits: list[str]


def evaluate_firebreak(
    task: TaskProfile,
    thresholds: Optional[FirebreakThresholds] = None,
) -> FirebreakDecision:
    """Evaluate task against interactive ceilings and route to Tier 1 or Tier 2/3."""
    if thresholds is None:
        thresholds = FirebreakThresholds()

    breaches: list[str] = []

    if task.requires_compiled_toolchain:
        breaches.append("requires_compiled_toolchain (ESP32/Docker/C++/Rust toolchain build)")

    if task.is_iterative_tdd:
        breaches.append("is_iterative_tdd (multi-turn edit-test-fix loop)")

    if task.estimated_steps > thresholds.interactive_max_steps:
        breaches.append(
            f"estimated_steps ({task.estimated_steps}) > interactive_max_steps ({thresholds.interactive_max_steps})"
        )

    if task.estimated_duration_seconds > thresholds.interactive_max_duration_seconds:
        breaches.append(
            f"estimated_duration_seconds ({task.estimated_duration_seconds:.1f}s) > "
            f"interactive_max_duration_seconds ({thresholds.interactive_max_duration_seconds:.1f}s)"
        )

    if task.files_inspected > thresholds.interactive_max_files_inspected:
        breaches.append(
            f"files_inspected ({task.files_inspected}) > "
            f"interactive_max_files_inspected ({thresholds.interactive_max_files_inspected})"
        )

    if breaches:
        return FirebreakDecision(
            task_name=task.name,
            tier=ExecutionTier.WORKER_OFFLOAD,
            reason="Tripped interactive firebreak ceilings: task routed to isolated git worktree worker.",
            breached_limits=breaches,
        )

    return FirebreakDecision(
        task_name=task.name,
        tier=ExecutionTier.INLINE_FASTPATH,
        reason="Within interactive ceilings: safe for Tier 1 inline execution.",
        breached_limits=[],
    )


@dataclasses.dataclass
class SymbolSlice:
    """AST-extracted source code slice for a target function or class."""
    symbol_name: str
    kind: str  # "function" or "class"
    file_path: str
    start_line: int
    end_line: int
    source_slice: str


def extract_ast_symbol_slices(
    file_path: Path,
    symbol_names: list[str],
) -> list[SymbolSlice]:
    """Parse a Python source file using AST and extract line-sliced source blocks for target symbols."""
    if not file_path.is_file():
        raise FileNotFoundError(f"Source file not found: {file_path}")

    source = file_path.read_text(encoding="utf-8")
    lines = source.splitlines()

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as e:
        raise ValueError(f"Failed to parse AST for {file_path}: {e}") from e

    target_set = set(symbol_names)
    slices: list[SymbolSlice] = []

    def _visit_node(node: ast.AST, parent_prefix: str = ""):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            full_name = f"{parent_prefix}{node.name}" if parent_prefix else node.name
            kind = "class" if isinstance(node, ast.ClassDef) else "function"

            if full_name in target_set or node.name in target_set:
                start = node.lineno
                end = getattr(node, "end_lineno", start)
                # Slice lines (1-indexed to 0-indexed)
                slice_lines = lines[start - 1 : end]
                symbol_source = "\n".join(slice_lines)

                slices.append(
                    SymbolSlice(
                        symbol_name=full_name,
                        kind=kind,
                        file_path=str(file_path),
                        start_line=start,
                        end_line=end,
                        source_slice=symbol_source,
                    )
                )

            # Visit nested symbols (methods inside classes)
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    _visit_node(child, parent_prefix=f"{full_name}.")

    for statement in tree.body:
        _visit_node(statement)

    return slices


def build_turn_zero_worker_payload(
    chunk_id: str,
    worktree_path: str,
    target_files: list[str],
    symbol_slices: list[SymbolSlice],
    objective: str,
) -> dict[str, Any]:
    """Assemble the Turn-0 worker payload with pre-hydrated AST symbol slices."""
    slices_data = [
        {
            "symbol": s.symbol_name,
            "kind": s.kind,
            "file": s.file_path,
            "line_range": f"L{s.start_line}-L{s.end_line}",
            "source": s.source_slice,
        }
        for s in symbol_slices
    ]

    return {
        "contract_version": 1,
        "chunk_id": chunk_id,
        "worktree": worktree_path,
        "objective": objective,
        "allowed_target_files": target_files,
        "turn_zero_prehydrated_symbols": slices_data,
        "worker_guidance": (
            "Turn-0 symbols are pre-hydrated above. "
            "Proceed directly to target editing and verification without exploratory greps or file traversal."
        ),
    }


def format_turn_zero_prompt(payload: dict[str, Any]) -> str:
    """Format the worker contract payload into an initial Turn-0 prompt string."""
    symbols = payload.get("turn_zero_prehydrated_symbols", [])
    symbol_blocks = []
    for item in symbols:
        symbol_blocks.append(
            f"### Symbol: `{item['symbol']}` ({item['kind']}) in `{item['file']}` [{item['line_range']}]\n"
            f"```python\n{item['source']}\n```"
        )

    symbols_section = "\n\n".join(symbol_blocks) if symbol_blocks else "*(No specific symbol slices requested)*"

    return f"""# FORGE Worker Contract — Chunk: {payload['chunk_id']}
**Objective:** {payload['objective']}
**Isolated Worktree:** `{payload['worktree']}`
**Allowed Target Files:** {', '.join(f'`{f}`' for f in payload['allowed_target_files'])}

## Turn-0 Target Symbol Slices (Pre-Hydrated)
{symbols_section}

## Invariant Directives
- {payload['worker_guidance']}
- Run deterministic verification before concluding turn.
"""


def run_self_test() -> None:
    """Execute complete self-test of the firebreak evaluation and Turn-0 AST symbol hydration."""
    print("================================================================================")
    print("Worker Firebreak & Turn-0 AST Pre-Hydration Recipe — Verification")
    print("================================================================================")

    # 1. Test firebreak decision matrix
    thresholds = FirebreakThresholds(
        interactive_max_steps=3,
        interactive_max_duration_seconds=45.0,
        interactive_max_files_inspected=10,
    )

    scenarios = [
        TaskProfile(
            name="surgical_typo_fix",
            estimated_steps=2,
            estimated_duration_seconds=12.0,
            files_inspected=1,
        ),
        TaskProfile(
            name="step_limit_overflow",
            estimated_steps=5,
            estimated_duration_seconds=30.0,
            files_inspected=2,
        ),
        TaskProfile(
            name="duration_limit_overflow",
            estimated_steps=2,
            estimated_duration_seconds=60.0,
            files_inspected=2,
        ),
        TaskProfile(
            name="broad_refactor_file_limit",
            estimated_steps=3,
            estimated_duration_seconds=40.0,
            files_inspected=14,
        ),
        TaskProfile(
            name="embedded_firmware_build",
            estimated_steps=2,
            estimated_duration_seconds=20.0,
            files_inspected=3,
            requires_compiled_toolchain=True,
        ),
        TaskProfile(
            name="iterative_tdd_refactor",
            estimated_steps=3,
            estimated_duration_seconds=30.0,
            files_inspected=4,
            is_iterative_tdd=True,
        ),
    ]

    print("\n[Phase 1] Evaluating Dual-Threshold Firebreak Decisions:")
    for task in scenarios:
        dec = evaluate_firebreak(task, thresholds)
        status_tag = "INLINE (Tier 1)" if dec.tier == ExecutionTier.INLINE_FASTPATH else "OFFLOAD (Tier 2/3)"
        print(f"  - [{status_tag:18}] Task: {task.name:<28}")
        if dec.breached_limits:
            for b in dec.breached_limits:
                print(f"      * Breached: {b}")

    # Assertions on firebreak decisions
    assert evaluate_firebreak(scenarios[0], thresholds).tier == ExecutionTier.INLINE_FASTPATH
    assert evaluate_firebreak(scenarios[1], thresholds).tier == ExecutionTier.WORKER_OFFLOAD
    assert evaluate_firebreak(scenarios[2], thresholds).tier == ExecutionTier.WORKER_OFFLOAD
    assert evaluate_firebreak(scenarios[3], thresholds).tier == ExecutionTier.WORKER_OFFLOAD
    assert evaluate_firebreak(scenarios[4], thresholds).tier == ExecutionTier.WORKER_OFFLOAD
    assert evaluate_firebreak(scenarios[5], thresholds).tier == ExecutionTier.WORKER_OFFLOAD
    print("  ✓ Firebreak decision matrix assertions passed cleanly.")

    # 2. Test Turn-0 AST Symbol Pre-Hydration
    print("\n[Phase 2] Extracting AST Symbol Slices from Target Codebase:")
    sample_module = '''"""Sample module for AST symbol pre-hydration."""

CONFIG_VERSION = 2

def compute_tax(amount: float, rate: float = 0.05) -> float:
    """Calculate simple tax rate."""
    if amount < 0:
        raise ValueError("Amount must be non-negative")
    return round(amount * rate, 2)

class OrderProcessor:
    """Processes customer orders with inventory checks."""

    def __init__(self, tax_rate: float = 0.05) -> None:
        self.tax_rate = tax_rate

    def checkout(self, items: list[dict[str, float]]) -> dict[str, float]:
        subtotal = sum(item["price"] for item in items)
        tax = compute_tax(subtotal, self.tax_rate)
        return {"subtotal": subtotal, "tax": tax, "total": subtotal + tax}

def unused_helper() -> None:
    pass
'''

    with tempfile.TemporaryDirectory(prefix="worker_firebreak_test_") as tmpdir:
        src_path = Path(tmpdir) / "orders.py"
        src_path.write_text(sample_module, encoding="utf-8")

        target_symbols = ["compute_tax", "OrderProcessor.checkout", "OrderProcessor"]
        slices = extract_ast_symbol_slices(src_path, target_symbols)

        print(f"  - Extracted {len(slices)} symbol slices from {src_path.name}:")
        for s in slices:
            print(f"      * {s.kind} `{s.symbol_name}` [L{s.start_line}-L{s.end_line}] ({len(s.source_slice.splitlines())} lines)")

        assert len(slices) == 3
        symbol_map = {s.symbol_name: s for s in slices}
        assert "compute_tax" in symbol_map
        assert "OrderProcessor" in symbol_map
        assert "OrderProcessor.checkout" in symbol_map
        assert "def compute_tax" in symbol_map["compute_tax"].source_slice
        assert "class OrderProcessor:" in symbol_map["OrderProcessor"].source_slice
        assert "def checkout" in symbol_map["OrderProcessor.checkout"].source_slice

        # 3. Assemble Turn-0 Worker Contract Payload
        print("\n[Phase 3] Generating Turn-0 Worker Contract Prompt Payload:")
        payload = build_turn_zero_worker_payload(
            chunk_id="chk-orders-v2-tax-rounding",
            worktree_path="/tmp/forge-wt-chk-orders-v2",
            target_files=["orders.py"],
            symbol_slices=slices,
            objective="Update compute_tax to support configurable decimal rounding places.",
        )

        prompt = format_turn_zero_prompt(payload)
        assert "compute_tax" in prompt
        assert "OrderProcessor" in prompt
        assert "Turn-0 symbols are pre-hydrated above." in prompt
        print(f"  ✓ Prompt assembled ({len(prompt)} chars, {len(prompt.splitlines())} lines).")
        print("  ✓ Ready for Turn-0 zero-search fast-path dispatch.")

    print("\n================================================================================")
    print("ALL TESTS PASSED: Worker firebreak and Turn-0 pre-hydration verified.")
    print("================================================================================")


def main() -> int:
    parser = argparse.ArgumentParser(description="Worker firebreak & Turn-0 AST pre-hydration")
    parser.add_argument("--demo", action="store_true", help="Run verification test suite")
    parser.add_argument("--json", action="store_true", help="Output sample worker payload as JSON")
    args = parser.parse_args()

    if args.json:
        # Quick JSON demonstration
        with tempfile.NamedTemporaryFile("w", suffix=".py") as tf:
            tf.write("def sample():\n    return 42\n")
            tf.flush()
            slices = extract_ast_symbol_slices(Path(tf.name), ["sample"])
            payload = build_turn_zero_worker_payload("demo-chunk", "/tmp/wt-demo", ["sample.py"], slices, "Demo")
            print(json.dumps(payload, indent=2))
        return 0

    run_self_test()
    return 0


if __name__ == "__main__":
    sys.exit(main())
