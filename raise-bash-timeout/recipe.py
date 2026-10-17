# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Check the timeout hook and wait-for.sh against their documented behaviour.

    uv run raise-bash-timeout/recipe.py
    python3 raise-bash-timeout/recipe.py

Runs the hook as a subprocess with synthetic PreToolUse JSON on stdin, then
runs wait-for.sh with a short condition. No network, nothing written outside
the process's own output. Prints one PASS line per check and exits nonzero on
any failure.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "raise_bash_timeout.py"
WAIT_FOR = HERE / "wait-for.sh"

failures = []


def check(label, ok, detail=""):
    if ok:
        print(f"PASS {label}")
    else:
        print(f"FAIL {label} {detail}".rstrip())
        failures.append(label)


def run_hook(stdin_text):
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin_text, capture_output=True, text=True, timeout=20,
    )
    return p.returncode, p.stdout.strip()


def pretooluse(tool, tool_input):
    return json.dumps({"hook_event_name": "PreToolUse",
                       "tool_name": tool, "tool_input": tool_input})


def hook_checks():
    # 1. A test runner with no timeout gets a raised timeout in the output.
    rc, out = run_hook(pretooluse("Bash", {"command": "python3 -m pytest -q"}))
    updated = None
    if out:
        try:
            updated = json.loads(out)["hookSpecificOutput"]["updatedInput"]
        except (ValueError, KeyError):
            updated = None
    check("hook raises unset timeout for pytest",
          rc == 0 and updated is not None and updated.get("timeout") == 600_000,
          f"rc={rc} out={out[:200]}")
    check("hook leaves the command itself unchanged",
          updated is not None and updated.get("command") == "python3 -m pytest -q",
          f"updated={updated}")

    # 2. A command with an explicit timeout gets no output, however low.
    rc, out = run_hook(pretooluse("Bash", {"command": "pytest -q", "timeout": 30_000}))
    check("hook leaves an explicit timeout alone", rc == 0 and out == "",
          f"rc={rc} out={out[:200]}")

    # 3. A non-Bash tool gets no output even if its input looks like a test run.
    rc, out = run_hook(pretooluse("Read", {"file_path": "/tmp/pytest.log"}))
    check("hook ignores non-Bash tools", rc == 0 and out == "",
          f"rc={rc} out={out[:200]}")

    # 4. Malformed stdin fails open: exit 0, no output.
    rc, out = run_hook("{not json")
    check("hook fails open on malformed stdin", rc == 0 and out == "",
          f"rc={rc} out={out[:200]}")


def wait_for_checks():
    def wf(args):
        start = time.monotonic()
        p = subprocess.run(["bash", str(WAIT_FOR), *args],
                           capture_output=True, text=True, cwd=str(HERE), timeout=60)
        return p.returncode, time.monotonic() - start, p.stderr.strip()

    # 5. A met condition exits 0.
    rc, _, err = wf(["true", "--timeout", "5"])
    check("wait-for.sh exits 0 when the condition is met", rc == 0, f"rc={rc} {err}")

    # 6. An unmet condition exits 124 at the timeout, and names the condition.
    rc, elapsed, err = wf(["false", "--timeout", "2", "--interval", "1"])
    check("wait-for.sh exits 124 on timeout", rc == 124, f"rc={rc} {err}")
    check("wait-for.sh timeout names the condition", "'false'" in err or " false" in err,
          f"stderr={err}")

    # 7. A wait above the 110s cap is refused quickly without --long.
    rc, elapsed, err = wf(["true", "--timeout", "120"])
    check("wait-for.sh refuses >110s timeout without --long",
          rc == 2 and elapsed < 5, f"rc={rc} elapsed={elapsed:.2f}s")


def main():
    hook_checks()
    wait_for_checks()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
