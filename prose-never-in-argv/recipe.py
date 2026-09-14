# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Demonstrate that prose in argv is unsafe, and that quoting does not fix it.

    uv run prose-never-in-argv/recipe.py
    python3 prose-never-in-argv/recipe.py

Every case runs a real shell. Nothing here touches anything outside a
temp directory, and the "command substitution" case executes `id -un`
only, to prove execution happened.
"""

import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

# An entirely ordinary sentence. Nothing here is adversarial.
PROSE = "Ian's build failed (PR #46) — see `id -un` for the runner"


def sh(script: str) -> tuple[int, str]:
    """Run a script through a real shell, as a cron line or an eval would."""
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def show(label: str, script: str, safe: bool) -> bool:
    rc, out = sh(script)
    # Intact means BOTH: the shell did not error, and what came back is
    # byte-for-byte the prose we started with. Checking only "is the prose
    # in the output" is not enough — bash echoes the offending script back
    # inside its own syntax-error message, which trivially contains it.
    intact = rc == 0 and out == PROSE
    if rc != 0:
        verdict = f"SHELL ERROR (rc={rc}) — the job dies here"
    elif not intact:
        verdict = "PROSE MANGLED — it ran, and produced the wrong text"
    else:
        verdict = "prose survived intact"
    print(f"\n--- {label}")
    print(f"    script : {script[:96]}")
    print(f"    rc     : {rc}")
    print(f"    output : {out[:200]!r}")
    print(f"    verdict: {verdict}")
    return intact if safe else (not intact)


def main() -> int:
    results = []

    # 1. The anti-pattern: prose interpolated straight into command text.
    #    This is what building a command as a string and running it does.
    results.append(show(
        "ANTI-PATTERN: prose interpolated into command text",
        f'echo "{PROSE}"',
        safe=False,
    ))

    # 2. The reflex fix — "just put it in double quotes" — is ALREADY what
    #    case 1 did. Backticks expand inside double quotes, so the path in
    #    the prose is executed. Single quotes fail differently: the
    #    apostrophe in "Ian's" closes the quote.
    results.append(show(
        "ANTI-PATTERN: single quotes (the apostrophe closes them)",
        f"echo '{PROSE}'",
        safe=False,
    ))

    with tempfile.TemporaryDirectory() as td:
        msg = Path(td) / "msg.md"
        msg.write_text(PROSE, encoding="utf-8")

        # 3. THE FIX: write to a file, pass the path. The prose never
        #    enters command text at all, so there is nothing to parse.
        results.append(show(
            "FIX: write to a file, pass the path",
            f"cat {shlex.quote(str(msg))}",
            safe=True,
        ))

        # 4. Also safe: command substitution. Its OUTPUT is not re-parsed
        #    for shell metacharacters. Use when a tool has no --file flag,
        #    or when its --file means something else (attachments, etc).
        results.append(show(
            "FIX: command substitution — output is not re-parsed",
            f'echo "$(cat {shlex.quote(str(msg))})"',
            safe=True,
        ))

        # 5. Also safe, and the right answer when you control the caller:
        #    skip the shell entirely. No shell, no shell syntax.
        p = subprocess.run(["echo", PROSE], capture_output=True, text=True)
        intact = PROSE in p.stdout
        print("\n--- FIX: no shell at all (argv list, shell=False)")
        print(f"    call   : subprocess.run(['echo', PROSE])")
        print(f"    output : {p.stdout.strip()[:200]!r}")
        print(f"    verdict: {'prose survived intact' if intact else 'PROSE MANGLED'}")
        results.append(intact)

    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} cases behaved as documented.")
    if passed != len(results):
        print("A case did not behave as this pattern claims — do not trust the entry.")
        return 1
    print("Takeaway: quoting is not the fix. Keeping prose out of command text is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
