# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
recipe.py — Deterministic pre-commit gate with path-scoped verification mapping,
Maker-Checker barrier, and live zero-bypass canary guard probes.

Demonstrates the three core components of the Aerial + Zero pre-commit gate:
  1. Path-Scoped Verification Mapping: Evaluates staged files against glob patterns
     to dynamically execute only relevant linters, typecheckers, and test suites,
     preventing unconstrained full-suite timeouts or backend linter failures on
     frontend diffs.
  2. Live Zero-Bypass Guard Probe: Injects a synthetic canary violation into the
     security/safety scanner before scanning actual files. If the scanner fails
     to reject the canary (due to rule bitrot, bad regex, or missing env config),
     the gate aborts immediately with a bypass error rather than falsely passing.
  3. Maker-Checker Exit-Code Barrier: An independent gate evaluating the staged
     diffs produced by the agent ('Maker'), enforcing binary pass/fail exit codes
     prior to any git commit or push.

Usage:
  uv run deterministic-precommit-gate/recipe.py
  python3 deterministic-precommit-gate/recipe.py
"""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import os
import re
import sys
import tempfile
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


class GateResult(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    GUARD_BYPASS = "GUARD_BYPASS"


@dataclasses.dataclass(frozen=True)
class CheckDefinition:
    """A path-scoped verification check mapped to specific file patterns."""
    name: str
    description: str
    globs: tuple[str, ...]
    checker_fn: Callable[[list[Path]], tuple[bool, str]]


@dataclasses.dataclass
class GateExecutionReport:
    """Report summarizing Maker-Checker gate outcome."""
    result: GateResult
    exit_code: int
    executed_checks: list[str]
    skipped_checks: list[str]
    violations: list[str]
    canary_probed: bool


class SecurityScanner:
    """Pre-commit security scanner checking for hardcoded secrets, tokens, and PII."""

    # Canonical forbidden secret patterns (simulated regex rules)
    DEFAULT_PATTERNS = {
        "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
        "generic_secret_token": re.compile(r"(?i)(api[_-]?key|secret[_-]?token)\s*=\s*['\"][0-9a-zA-Z\-_]{20,}['\"]"),
        "private_ipv4": re.compile(r"\b(192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"),
    }

    # Synthetic canary probe payload guaranteed to trigger the generic_secret_token pattern
    CANARY_PAYLOAD = 'export API_KEY="sk_live_canary_synthetic_probe_9876543210abcdef"'

    def __init__(self, patterns: Optional[dict[str, re.Pattern]] = None) -> None:
        self.patterns = patterns if patterns is not None else dict(self.DEFAULT_PATTERNS)

    def scan_text(self, text: str) -> list[str]:
        """Scan a text buffer for rule violations."""
        findings = []
        for name, pattern in self.patterns.items():
            matches = pattern.findall(text)
            if matches:
                findings.append(f"Matched rule '{name}': {len(matches)} occurrence(s)")
        return findings

    def scan_file(self, file_path: Path) -> list[str]:
        """Scan an on-disk file for violations."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            findings = self.scan_text(content)
            return [f"{file_path.name}: {f}" for f in findings]
        except Exception as e:
            return [f"{file_path.name}: Failed to read file: {e}"]

    def probe_canary_rejection(self) -> bool:
        """Zero-Bypass Probe: Verify that the scanner actively rejects synthetic canary violations.

        Returns True if the scanner detected and blocked the canary.
        Returns False if the canary passed through undetected (indicating rule bitrot/bypass).
        """
        canary_findings = self.scan_text(self.CANARY_PAYLOAD)
        return len(canary_findings) > 0


def filter_scoped_checks(
    staged_paths: list[Path],
    checks: list[CheckDefinition],
) -> tuple[list[CheckDefinition], list[CheckDefinition]]:
    """Partition configured checks into active (path-matched) and skipped checks."""
    active: list[CheckDefinition] = []
    skipped: list[CheckDefinition] = []

    staged_str_paths = [p.as_posix() for p in staged_paths]

    for check in checks:
        matches = False
        for path in staged_str_paths:
            for pattern in check.globs:
                if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern):
                    matches = True
                    break
            if matches:
                break

        if matches:
            active.append(check)
        else:
            skipped.append(check)

    return active, skipped


class MakerCheckerGate:
    """The independent gate barrier separating Maker (authoring) from Checker (validation)."""

    def __init__(
        self,
        security_scanner: SecurityScanner,
        checks: list[CheckDefinition],
    ) -> None:
        self.scanner = security_scanner
        self.checks = checks

    def evaluate(self, staged_files: list[Path]) -> GateExecutionReport:
        """Execute full Maker-Checker evaluation over staged files."""
        violations: list[str] = []

        # 1. Zero-Bypass Guard Probe: test the scanner with synthetic canary payload first
        canary_detected = self.scanner.probe_canary_rejection()
        if not canary_detected:
            return GateExecutionReport(
                result=GateResult.GUARD_BYPASS,
                exit_code=2,
                executed_checks=[],
                skipped_checks=[c.name for c in self.checks],
                violations=[
                    "CRITICAL: Zero-bypass canary probe FAILED. The security scanner did not detect "
                    "the synthetic canary secret payload. The guard is broken, unconfigured, or failing open! "
                    "Execution halted to prevent unvetted code admission."
                ],
                canary_probed=True,
            )

        # 2. Path-scoped check mapping
        active_checks, skipped_checks = filter_scoped_checks(staged_files, self.checks)

        # 3. Security scan on actual staged files
        for f in staged_files:
            file_violations = self.scanner.scan_file(f)
            violations.extend(file_violations)

        # 4. Execute active path-scoped checkers
        for check in active_checks:
            # Filter staged files relevant to this check
            relevant_files = [
                f for f in staged_files
                if any(fnmatch.fnmatch(f.name, g) or fnmatch.fnmatch(f.as_posix(), g) for g in check.globs)
            ]
            passed, err_msg = check.checker_fn(relevant_files)
            if not passed:
                violations.append(f"[{check.name}] {err_msg}")

        if violations:
            return GateExecutionReport(
                result=GateResult.FAILED,
                exit_code=1,
                executed_checks=[c.name for c in active_checks],
                skipped_checks=[c.name for c in skipped_checks],
                violations=violations,
                canary_probed=True,
            )

        return GateExecutionReport(
            result=GateResult.PASSED,
            exit_code=0,
            executed_checks=[c.name for c in active_checks],
            skipped_checks=[c.name for c in skipped_checks],
            violations=[],
            canary_probed=True,
        )


# --- Built-in Sample Checkers (Standard Library Only) ---

def check_python_syntax(files: list[Path]) -> tuple[bool, str]:
    """Check Python syntax using ast.parse."""
    import ast
    for f in files:
        if f.suffix == ".py":
            try:
                ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
            except SyntaxError as e:
                return False, f"Syntax error in {f.name}:{e.lineno} — {e.msg}"
    return True, "All Python files parsed successfully."


def check_markdown_fences(files: list[Path]) -> tuple[bool, str]:
    """Check that fenced code blocks in Markdown files specify language tags."""
    for f in files:
        if f.suffix == ".md":
            text = f.read_text(encoding="utf-8")
            lines = text.splitlines()
            in_fence = False
            for line_no, line in enumerate(lines, start=1):
                stripped = line.strip()
                if stripped.startswith("```"):
                    if not in_fence:
                        # Opening fence: must have language identifier
                        lang = stripped[3:].strip()
                        if not lang:
                            return False, f"Untagged opening code fence in {f.name}:{line_no}; must specify language tag."
                        in_fence = True
                    else:
                        # Closing fence
                        in_fence = False
    return True, "Markdown formatting valid."


def check_mock_frontend(files: list[Path]) -> tuple[bool, str]:
    """Mock scoped checker for TypeScript/JavaScript frontend assets."""
    for f in files:
        content = f.read_text(encoding="utf-8")
        if "console.log" in content:
            return False, f"Forbidden debug console.log found in frontend file {f.name}"
    return True, "Frontend assets clean."


def run_self_test() -> None:
    """Execute complete self-test of path-scoped mapping, canary probing, and Maker-Checker gate."""
    print("================================================================================")
    print("Deterministic Pre-Commit Gate Recipe — Verification")
    print("================================================================================")

    configured_checks = [
        CheckDefinition(
            name="python_syntax",
            description="Compiles Python AST to catch syntax errors",
            globs=("*.py",),
            checker_fn=check_python_syntax,
        ),
        CheckDefinition(
            name="frontend_lint",
            description="Audits JS/TS files for forbidden debug logs",
            globs=("*.ts", "*.tsx", "*.js"),
            checker_fn=check_mock_frontend,
        ),
        CheckDefinition(
            name="markdown_docs",
            description="Ensures all Markdown fences declare a language tag",
            globs=("*.md", "docs/*"),
            checker_fn=check_markdown_fences,
        ),
    ]

    with tempfile.TemporaryDirectory(prefix="precommit_test_") as tmpdir:
        root = Path(tmpdir)

        # 1. Test Path-Scoped Mapping
        print("\n[Phase 1] Testing Path-Scoped Verification Mapping:")
        file_md = root / "GUIDE.md"
        file_md.write_text("# Guide\n```python\nprint(1)\n```\n")

        file_py = root / "app.py"
        file_py.write_text("def hello() -> str:\n    return 'hello'\n")

        file_ts = root / "view.ts"
        file_ts.write_text("export const title: string = 'Welcome';\n")

        active, skipped = filter_scoped_checks([file_md], configured_checks)
        print(f"  - Staged: ['GUIDE.md'] -> Active: {[c.name for c in active]}, Skipped: {[c.name for c in skipped]}")
        assert [c.name for c in active] == ["markdown_docs"]
        assert "python_syntax" in [c.name for c in skipped]
        assert "frontend_lint" in [c.name for c in skipped]

        active_py, _ = filter_scoped_checks([file_py], configured_checks)
        assert [c.name for c in active_py] == ["python_syntax"]
        print("  ✓ Path-scoped mapping accurately isolates active runners without running irrelevant checks.")

        # 2. Test Live Zero-Bypass Guard Probe (Canary Injection)
        print("\n[Phase 2] Testing Live Zero-Bypass Guard Probe:")
        normal_scanner = SecurityScanner()
        probe_success = normal_scanner.probe_canary_rejection()
        print(f"  - Healthy scanner canary probe response: {probe_success} (Probe passed: canary detected)")
        assert probe_success is True

        # Simulate broken scanner (e.g. wiped patterns / regex bitrot / fail-open bug)
        broken_scanner = SecurityScanner(patterns={})
        gate_broken = MakerCheckerGate(broken_scanner, configured_checks)
        report_bypass = gate_broken.evaluate([file_py])
        print(f"  - Broken scanner gate outcome: {report_bypass.result} (exit_code={report_bypass.exit_code})")
        assert report_bypass.result == GateResult.GUARD_BYPASS
        assert report_bypass.exit_code == 2
        assert "Zero-bypass canary probe FAILED" in report_bypass.violations[0]
        print("  ✓ Zero-bypass guard probe detected scanner failure and blocked execution immediately.")

        # 3. Test Clean Staged Changeset (Gate Pass)
        print("\n[Phase 3] Testing Clean Staged Changeset (Maker-Checker Gate Pass):")
        healthy_gate = MakerCheckerGate(normal_scanner, configured_checks)
        clean_report = healthy_gate.evaluate([file_md, file_py, file_ts])
        print(f"  - Clean changeset result: {clean_report.result} (exit_code={clean_report.exit_code})")
        print(f"    Active checks: {clean_report.executed_checks}")
        assert clean_report.result == GateResult.PASSED
        assert clean_report.exit_code == 0
        assert len(clean_report.violations) == 0
        print("  ✓ Clean changes successfully passed Maker-Checker gate.")

        # 4. Test Violations Detection (Secret & Syntax)
        print("\n[Phase 4] Testing Violation Detection (Maker-Checker Gate Failure):")
        bad_py = root / "bad.py"
        bad_py.write_text("def broken_syntax(:\n")  # Syntax error

        secret_file = root / "config.py"
        secret_file.write_text("HOST_IP = '192.168.1.50'\nSECRET_TOKEN = 'abcdef1234567890abcdef1234567890'\n")

        fail_report = healthy_gate.evaluate([bad_py, secret_file])
        print(f"  - Violation changeset result: {fail_report.result} (exit_code={fail_report.exit_code})")
        for v in fail_report.violations:
            print(f"      * Violation caught: {v}")
        assert fail_report.result == GateResult.FAILED
        assert fail_report.exit_code == 1
        assert any("Syntax error" in v for v in fail_report.violations)
        assert any("Matched rule" in v for v in fail_report.violations)
        print("  ✓ All syntax and security violations caught deterministically.")

    print("\n================================================================================")
    print("ALL TESTS PASSED: Path scoping, canary probe, and Maker-Checker gate verified.")
    print("================================================================================")


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic pre-commit gate")
    parser.add_argument("--demo", action="store_true", help="Run verification test suite")
    parser.add_argument(
        "--scan",
        nargs="*",
        help="Scan specific file paths directly with the Maker-Checker gate",
    )
    args = parser.parse_args()

    if args.scan:
        paths = [Path(p) for p in args.scan]
        scanner = SecurityScanner()
        gate = MakerCheckerGate(
            scanner,
            [
                CheckDefinition("python_syntax", "Check python syntax", ("*.py",), check_python_syntax),
                CheckDefinition("markdown_docs", "Check markdown fences", ("*.md",), check_markdown_fences),
            ],
        )
        report = gate.evaluate(paths)
        print(f"Gate outcome: {report.result.value} (exit {report.exit_code})")
        for v in report.violations:
            print(f"  - {v}")
        return report.exit_code

    run_self_test()
    return 0


if __name__ == "__main__":
    sys.exit(main())
