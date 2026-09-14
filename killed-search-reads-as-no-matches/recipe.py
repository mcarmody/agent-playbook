# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Show that a killed search and a clean search are indistinguishable by output.

    uv run killed-search-reads-as-no-matches/recipe.py
    python3 killed-search-reads-as-no-matches/recipe.py

Three searches over a small temp tree: one that matches, one that
genuinely finds nothing, and one killed before it finishes. The last two
produce byte-identical output. Only the exit code separates them.

On the killed case: the wait is simulated (`sleep` in front of the grep)
rather than produced by making the tree big enough to actually be slow.
That is deliberate — a timing-dependent demo passes or fails based on the
page cache and the disk of whoever runs it, which is a poor way to
demonstrate a claim about determinism. The kill path is identical either
way: the process is SIGTERMed with nothing on stdout yet.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT_EXITS = {124, 125, 137, 143}  # GNU timeout, SIGKILL, SIGTERM


class SearchKilled(RuntimeError):
    """The search did not finish. Its empty result means nothing."""


def _run(pattern: str, root: Path, timeout_s: float, stall_s: float = 0.0):
    inner = f"grep -r {pattern!r} {str(root)!r}"
    if stall_s:
        inner = f"sleep {stall_s}; {inner}"
    p = subprocess.run(
        ["timeout", "-s", "TERM", str(timeout_s), "bash", "-c", inner],
        capture_output=True, text=True,
    )
    return p.returncode, [l for l in p.stdout.splitlines() if l.strip()]


def search(pattern: str, root: Path, timeout_s: float, stall_s: float = 0.0) -> list[str]:
    """Grep a tree, refusing to report a killed run as a clean one.

    THIS is the part worth copying into your own tooling. The three-way
    split on returncode is the whole pattern: 0 found, 1 genuinely absent,
    anything else unknown — and unknown must raise, not return [].
    """
    rc, hits = _run(pattern, root, timeout_s, stall_s)
    if rc in TIMEOUT_EXITS or rc > 128:
        raise SearchKilled(
            f"search for {pattern!r} killed after {timeout_s}s (exit {rc}); "
            "an empty result here is not evidence of absence"
        )
    if rc > 1:
        raise RuntimeError(f"search failed (exit {rc})")
    return hits


def build_tree(root: Path, n_files: int = 300) -> None:
    filler = "lorem ipsum dolor sit amet consectetur " * 40
    for i in range(n_files):
        d = root / f"pkg{i % 20}" / f"sub{i % 6}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"mod{i}.txt").write_text("\n".join(filler for _ in range(20)), encoding="utf-8")
    (root / "pkg0" / "sub0" / "target.txt").write_text(
        "call_site: deprecated_api_v1(payload)\n", encoding="utf-8"
    )


def main() -> int:
    if subprocess.run(["which", "timeout"], capture_output=True).returncode != 0:
        print("needs GNU `timeout` on PATH; skipping.")
        return 0

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_tree(root)

        print("=== the naive call: exit code discarded, output believed ===")
        cases = [
            ("pattern that IS present",                 "deprecated_api_v1",          30.0, 0.0),
            ("pattern that is genuinely ABSENT",        "pattern_that_does_not_exist", 30.0, 0.0),
            ("same absent pattern, but KILLED first",   "pattern_that_does_not_exist",  0.2, 5.0),
        ]
        seen = []
        for label, pat, t, stall in cases:
            rc, hits = _run(pat, root, t, stall)
            seen.append((rc, hits))
            print(f"\n  {label}")
            print(f"    exit code   : {rc}")
            print(f"    lines found : {len(hits)}")
            print(f"    naive read  : {'FOUND' if hits else 'not present — sweep clean'}")

        (clean_rc, clean_hits), (killed_rc, killed_hits) = seen[1], seen[2]
        print("\n=== the two empty results, side by side ===")
        print(f"  genuinely absent : exit {clean_rc}, {len(clean_hits)} lines")
        print(f"  killed mid-search: exit {killed_rc}, {len(killed_hits)} lines")
        same_output = len(clean_hits) == len(killed_hits) == 0
        rc_differs = clean_rc != killed_rc
        print(f"  same output?     : {same_output}   <- why this is silent")
        print(f"  same exit code?  : {not rc_differs}   <- the only thing that tells you")

        print("\n=== the guarded call refuses the killed one ===")
        try:
            search("pattern_that_does_not_exist", root, 0.2, stall_s=5.0)
            print("  guard did NOT fire — do not trust this entry")
            guard_fired = False
        except SearchKilled as e:
            print(f"  SearchKilled: {e}")
            guard_fired = True

        print("\n=== positive control: prove the search can find anything at all ===")
        try:
            hits = search("deprecated_api_v1", root, 30.0)
            control_ok = bool(hits)
            print(f"  {len(hits)} line(s) found — the search works, so a miss means something")
        except SearchKilled:
            control_ok = False
            print("  the control itself was killed — raise the ceiling before trusting any miss")

        print("\n=== and the real negative, now worth believing ===")
        absent = search("pattern_that_does_not_exist", root, 30.0)
        negative_ok = absent == []
        print(f"  {len(absent)} line(s) — ran to completion, genuinely absent")

    ok = same_output and rc_differs and guard_fired and control_ok and negative_ok
    print("\n" + ("PASS: " if ok else "FAIL: ") + (
        "a killed search is byte-identical to a clean one; only the exit code tells you."
        if ok else "behaviour did not match the entry — do not trust it."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
