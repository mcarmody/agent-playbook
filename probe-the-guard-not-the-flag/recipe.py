# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Show that a flag-reading test cannot tell a wired guard from an unwired one.

    uv run probe-the-guard-not-the-flag/recipe.py
    python3 probe-the-guard-not-the-flag/recipe.py

The "guard" is a function that refuses writes outside an allowed directory.
Two copies: one wired into the write path, one defined but never called. Both
report guard_enabled = True. A test that reads the flag passes for both. A
test that attempts a forbidden write and checks the side effect separates them.
"""

import sys
import tempfile
from pathlib import Path


class Guard:
    """A write guard. `enabled` is the flag; `wired` is whether writes pass through it."""

    def __init__(self, allowed: Path, wired: bool):
        self.allowed = allowed
        self.enabled = True  # the flag that says the guard is on
        self.wired = wired

    def check(self, path: Path) -> bool:
        return self.allowed in path.parents

    def write(self, path: Path, text: str) -> None:
        if self.wired and not self.check(path):
            raise PermissionError(f"refused: {path}")
        path.write_text(text)


def flag_test(guard: Guard) -> bool:
    return guard.enabled is True


def probe_test(guard: Guard, scratch: Path) -> bool:
    allowed_file = guard.allowed / "ok.txt"
    forbidden_file = scratch / "forbidden.txt"

    guard.write(allowed_file, "x")  # positive control: allowed action succeeds
    if not allowed_file.exists():
        return False

    try:
        guard.write(forbidden_file, "x")
    except PermissionError:
        pass
    return not forbidden_file.exists()  # side effect must not have happened


def main() -> int:
    results = {}
    for label, wired in (("wired", True), ("unwired", False)):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            allowed = scratch / "allowed"
            allowed.mkdir()
            guard = Guard(allowed, wired)
            results[label] = (flag_test(guard), probe_test(guard, scratch))

    for label, (flag, probe) in results.items():
        print(f"{label:8} flag test: {'pass' if flag else 'FAIL'}   probe test: {'pass' if probe else 'FAIL'}")

    flag_blind = results["wired"][0] == results["unwired"][0]
    probe_sees = results["wired"][1] and not results["unwired"][1]
    if flag_blind and probe_sees:
        print("flag test could not tell the two guards apart; probe test could")
        return 0
    print("unexpected result")
    return 1


if __name__ == "__main__":
    sys.exit(main())
