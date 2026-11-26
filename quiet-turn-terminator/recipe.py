# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Decision function for a quiet-turn stop hook, with its cases asserted.

    uv run quiet-turn-terminator/recipe.py
    python3 quiet-turn-terminator/recipe.py

`decide` returns ("allow", "") or ("block", instruction). The caller maps
that onto its harness's stop-hook protocol.
"""

import sys

FALLBACK = "PASS"
INSTRUCTION = (
    "Quiet turn: the final message must be empty. Send no text. If the "
    "harness cannot send an empty message, reply with exactly the single "
    f"word {FALLBACK}."
)


def decide(final_text: str, quiet_turn: bool, already_blocked: bool):
    if not quiet_turn:
        return "allow", ""             # real turns are not this hook's business
    text = final_text.strip()
    if text in ("", FALLBACK):
        return "allow", ""
    if already_blocked:
        return "allow", ""             # loop guard: never block twice in a turn
    return "block", INSTRUCTION


def main() -> int:
    cases = [
        # (final text, quiet, already blocked) -> expected decision
        (("", True, False), "allow"),
        ((FALLBACK, True, False), "allow"),
        (("  PASS\n", True, False), "allow"),
        (("nothing needs me", True, False), "block"),
        (("checked both channels, all quiet", True, False), "block"),
        (("nothing needs me", True, True), "allow"),   # loop guard
        (("a real answer", False, False), "allow"),    # not a quiet turn
    ]
    for args, expected in cases:
        got, msg = decide(*args)
        assert got == expected, (args, got, expected)
        assert (msg != "") == (got == "block"), (args, msg)
        print(f"{expected:5}  quiet={args[1]!s:5} blocked={args[2]!s:5} {args[0]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
