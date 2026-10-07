# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Demo: run both Stop-hook guards against a synthetic transcript.

    uv run stop-hook-guards/recipe.py
    python3 stop-hook-guards/recipe.py

Shows, with real subprocess runs of the hooks exactly as Claude Code would
invoke them: a block, the same turn being let through on redraft (once per
turn), a clean reply passing, and a broken hook input failing open. Nothing
is written outside a temp directory.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def transcript(tmp: Path, reply: str, user: str) -> Path:
    p = tmp / "t.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "message": {"role": "user", "content": user}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": reply}]}},
    ]) + "\n")
    return p


def run(script: str, payload, env: dict, raw: str = None) -> str:
    r = subprocess.run([sys.executable, str(HERE / script)],
                       input=raw if raw is not None else json.dumps(payload),
                       capture_output=True, text=True, env={**os.environ, **env}, timeout=30)
    assert r.returncode == 0, "a guard must always exit 0"
    return r.stdout.strip() or "(no output: stop allowed)"


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        env = {"STOP_GUARD_STATE_DIR": str(tmp / "state")}
        bare = transcript(tmp, "PR #7 is merged", "status?")
        p = {"session_id": "demo", "transcript_path": str(bare)}

        print("1. bare id, first stop:        ", run("stop_ids_carry_names.py", p, env))
        print("2. same turn, redraft still bare:", run("stop_ids_carry_names.py", p, env))
        named = transcript(tmp, "PR #7, the retry-logic fix, is merged", "status? (2)")
        p2 = {"session_id": "demo", "transcript_path": str(named)}
        print("3. named id:                   ", run("stop_ids_carry_names.py", p2, env))
        print("4. garbage on stdin:           ", run("stop_ids_carry_names.py", None, env, raw="not json"))

        # stale check, with a fetch plugged in from a throwaway module
        (tmp / "demofetch.py").write_text(
            "def fetch(since_id, payload):\n"
            "    return [{'id': '2', 'author': 'alice', 'content': 'use the staging key'}]\n")
        (tmp / "state" / "baseline").mkdir(parents=True)
        (tmp / "state" / "baseline" / "demo").write_text("1\n")
        env2 = {**env, "STALE_CHECK_FETCH": "demofetch:fetch", "PYTHONPATH": str(tmp)}
        print("5. new messages arrived:\n" + run("stop_new_messages_check.py", p, env2))
        print("6. same turn again:            ", run("stop_new_messages_check.py", p, env2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
