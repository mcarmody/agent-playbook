"""Tests for stop_new_messages_check.py - run: python3 -m pytest test_stop_new_messages_check.py

The fetch step is plugged in through STALE_CHECK_FETCH (a module in this
directory, written by the fixture), so no chat platform is involved.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).parent / "stop_new_messages_check.py"

FETCH_SRC = '''
import json, os
def fetch(since_id, payload):
    if os.environ.get("FAKE_FETCH_RAISES"):
        raise RuntimeError("chat API down")
    return json.loads(os.environ.get("FAKE_MESSAGES", "[]"))
'''


def _transcript(tmp_path, reply="draft reply", user="hello"):
    p = tmp_path / "t.jsonl"
    events = [
        {"type": "user", "message": {"role": "user", "content": user}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": reply}]}},
    ]
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return p


def _run(tmp_path, payload, messages=(), baseline="100", **env_extra):
    state = tmp_path / "state"
    if baseline is not None and payload.get("session_id"):
        (state / "baseline").mkdir(parents=True, exist_ok=True)
        (state / "baseline" / payload["session_id"]).write_text(baseline + "\n")
    (tmp_path / "fakefetch.py").write_text(FETCH_SRC)
    env = {**os.environ, "STOP_GUARD_STATE_DIR": str(state),
           "STALE_CHECK_FETCH": "fakefetch:fetch",
           "PYTHONPATH": str(tmp_path), "FAKE_MESSAGES": json.dumps(list(messages)),
           **env_extra}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=60)


NEW = [{"id": "101", "author": "alice", "content": "actually, use the staging key"},
       {"id": "102", "author": "bob", "content": "+1"}]


def test_blocks_with_plain_label_and_messages(tmp_path):
    tp = _transcript(tmp_path)
    r = _run(tmp_path, {"session_id": "s1", "transcript_path": str(tp)}, NEW)
    out = json.loads(r.stdout)
    assert out["decision"] == "block"
    assert out["reason"].splitlines()[0].startswith("These are new messages posted while you were")
    assert "alice: actually, use the staging key" in out["reason"]
    assert "bob: +1" in out["reason"]
    # no steering: nothing beyond label and messages
    assert len(out["reason"].splitlines()) == 4


def test_blocks_once_per_turn(tmp_path):
    tp = _transcript(tmp_path)
    p = {"session_id": "s2", "transcript_path": str(tp)}
    assert json.loads(_run(tmp_path, p, NEW).stdout)["decision"] == "block"
    assert _run(tmp_path, p, NEW).stdout.strip() == ""  # same turn: marker holds


def test_stop_hook_active_lets_through(tmp_path):
    tp = _transcript(tmp_path)
    p = {"session_id": "s3", "transcript_path": str(tp), "stop_hook_active": True}
    assert _run(tmp_path, p, NEW).stdout.strip() == ""


def test_no_new_messages_passes(tmp_path):
    tp = _transcript(tmp_path)
    assert _run(tmp_path, {"session_id": "s4", "transcript_path": str(tp)}, []).stdout.strip() == ""


def test_baseline_message_itself_is_not_new(tmp_path):
    tp = _transcript(tmp_path)
    same = [{"id": "100", "author": "alice", "content": "the one I claimed"}]
    assert _run(tmp_path, {"session_id": "s5", "transcript_path": str(tp)}, same).stdout.strip() == ""


def test_own_posts_filtered(tmp_path):
    tp = _transcript(tmp_path)
    mine = [{"id": "101", "author": "me-bot", "content": "my own earlier post"}]
    r = _run(tmp_path, {"session_id": "s6", "transcript_path": str(tp)}, mine, SELF_AUTHOR="me-bot")
    assert r.stdout.strip() == ""


def test_no_baseline_means_not_a_chat_turn(tmp_path):
    tp = _transcript(tmp_path)
    r = _run(tmp_path, {"session_id": "s7", "transcript_path": str(tp)}, NEW, baseline=None)
    assert r.stdout.strip() == ""


def test_fetch_error_fails_open(tmp_path):
    tp = _transcript(tmp_path)
    r = _run(tmp_path, {"session_id": "s8", "transcript_path": str(tp)}, NEW, FAKE_FETCH_RAISES="1")
    assert r.returncode == 0 and r.stdout.strip() == ""
    assert "failing open" in r.stderr


def test_bad_inputs_fail_open(tmp_path):
    assert _run(tmp_path, {}).stdout.strip() == ""
    assert _run(tmp_path, {"session_id": "s9", "transcript_path": "/nonexistent"}, NEW).stdout.strip() == ""
    r = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_default_stub_is_inert(tmp_path):
    tp = _transcript(tmp_path)
    state = tmp_path / "state"
    (state / "baseline").mkdir(parents=True)
    (state / "baseline" / "s10").write_text("100")
    env = {k: v for k, v in os.environ.items() if k != "STALE_CHECK_FETCH"}
    env["STOP_GUARD_STATE_DIR"] = str(state)
    r = subprocess.run([sys.executable, str(HOOK)],
                       input=json.dumps({"session_id": "s10", "transcript_path": str(tp)}),
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.stdout.strip() == ""
