"""Tests for stop_ids_carry_names.py - run: python3 -m pytest test_stop_ids_carry_names.py

Positives are the shapes the rule exists for (bare ids, ids "named" only by a
status word, ranges, nested ids); negatives are named ids, ids named earlier
in the reply, and things that merely look like ids (code, URLs, mention
tokens, hex colors, ranks).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import stop_ids_carry_names as hook
from stop_ids_carry_names import find_unnamed_ids

HOOK = Path(__file__).parent / "stop_ids_carry_names.py"

POSITIVE = [
    ("myrepo PR #738 is up", ["#738"]),
    ("PR #732", ["#732"]),
    ("Issue #65 needs a look", ["#65"]),
    ("issue #65, ok", ["#65"]),
    ("myrepo PRs #739 and #740 merged", ["#739", "#740"]),
    ("myrepo PRs #605–#608 merged", ["#605–#608"]),
    ("PRs #605-#608 merged", ["#605–#608"]),
    ("PRs 605 through 608 merged", ["#605–#608"]),
    ("PR #7, merged and green", ["#7"]),
    ("PR #7 (merged)", ["#7"]),
    ("PR #7, and #8, the R2 reconnect tests", ["#7"]),
    ("so #3 can't land", ["#3"]),
    ("PR #56 already has my approval", ["#56"]),
    ("checklist 5a of #5", ["#5"]),
    ("PR #46, checklist 5a of #5: Mars order-book golden fixtures", ["#5"]),
    ("myrepo #12 is blocked", ["#12"]),
    ("myrepo#738 landed", ["#738"]),
    ("**PR #66** is done", ["#66"]),
    ("Closes #70 in the PR body", ["#70"]),
    ("First thing.\n\nSecond chunk mentions PR #91 alone.", ["#91"]),
    ("PR #5, the R2 tests. Then PR #6 too.", ["#6"]),
    ("PR #10: ok", ["#10"]),
    ("PR #10, which is merged", ["#10"]),
    ("PRs #1, #2 and #3 landed", ["#1", "#2", "#3"]),
]

NEGATIVE = [
    "PR #7, the R2 reconnect tests, is green",
    "PR #7: R2 reconnect tests",
    "PR #7 (the R2 reconnect tests) is green",
    "PR #7 — the R2 reconnect tests",
    "PR #7 - the R2 reconnect tests",
    'PR #7 "R2 reconnect tests" is green',
    'PR #7 “R2 reconnect tests” is green',
    'The "R2 reconnect tests" PR #7 is green',
    'The "R2 reconnect tests" (PR #7) is green',
    "PR #7, the R2 reconnect tests. PR #7 is green again.",
    "PR #7, the R2 reconnect tests, and #8, the tools consolidation, merged",
    "PR #738, the ids-carry-names stop hook: green. #738 merges next",
    "PRs #739, the kitchen consolidation, and #740, the layout cleanup",
    "PR #46, checklist 5a of #5 (Mars order-book golden fixtures)",
    "Nothing numbered here at all",
    "Look at `PR #738` in the code",
    "```\ngh pr view #738\n```\nno ids in prose",
    "Run `gh pr merge #738`",
    "https://github.com/example/myrepo/pull/738",
    "see https://example.com/page#12 for details",
    "see <https://example.com/#123>",
    "post in <#1234567890> please",
    "the channel #general and #random",
    "color #fff and #1a2b3c and #333333",
    "background: #000;",
    "Smile &#39;ok&#39;",
    "That is our #1 priority",
    "He ranked #2 overall",
    "Step #3 is next",
    "Room #12 is booked",
    "heading ## 5 things",
    "v2.4 shipped; build 1234",
    "",
]


@pytest.mark.parametrize("text,expected", POSITIVE)
def test_positive(text, expected):
    assert find_unnamed_ids(text) == expected, text


@pytest.mark.parametrize("text", NEGATIVE)
def test_negative(text):
    assert find_unnamed_ids(text) == [], text


def test_named_earlier_allows_bare_later():
    t = "PR #7, the R2 reconnect tests, is green.\n\nLater: #7 merged."
    assert find_unnamed_ids(t) == []


def test_named_later_does_not_excuse_earlier():
    t = "#7 merged. PR #7, the R2 reconnect tests."
    assert find_unnamed_ids(t) == ["#7"]


def test_thread_only_naming_not_in_reply():
    # An id named in an earlier message is not named in THIS reply.
    assert find_unnamed_ids("Issue #65 follow-up") == ["#65"]


def test_range_needs_every_endpoint_named_earlier():
    t = "PR #605, the tools doc, and PR #608, the layout doc, merged. PRs #605-#608 are done."
    assert find_unnamed_ids(t) == []
    t2 = "PR #605, the tools doc, merged. PRs #605-#608 are done."
    assert find_unnamed_ids(t2) == ["#605–#608"]


def test_dedup_and_order():
    assert find_unnamed_ids("#40 and #41 and #40 again") == ["#40", "#41"]


def test_reason_text():
    r = hook.build_reason(["#738", "#739"])
    assert r.startswith("ids carry names: #738, #739 appear without a name")


# ------------------------------------------------------------- hook process

def _transcript(tmp_path, reply, user="hello"):
    p = tmp_path / "t.jsonl"
    events = [
        {"type": "user", "message": {"role": "user", "content": user}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": reply}]}},
    ]
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return p


def _run(tmp_path, payload):
    env = {**os.environ, "STOP_GUARD_STATE_DIR": str(tmp_path / "state")}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=60)


def test_hook_blocks_once_then_lets_through(tmp_path):
    tp = _transcript(tmp_path, "myrepo PR #738 is up")
    payload = {"session_id": "s1", "transcript_path": str(tp), "stop_hook_active": False}
    r1 = _run(tmp_path, payload)
    out = json.loads(r1.stdout)
    assert out["decision"] == "block"
    assert out["reason"].startswith("ids carry names: #738 appear without a name")
    # Same turn, revised reply still bare: must not loop.
    payload["stop_hook_active"] = True
    r2 = _run(tmp_path, payload)
    assert r2.stdout.strip() == ""


def test_hook_new_turn_blocks_again(tmp_path):
    tp = _transcript(tmp_path, "PR #738 is up", user="first")
    _run(tmp_path, {"session_id": "s2", "transcript_path": str(tp)})
    tp = _transcript(tmp_path, "PR #739 is up", user="second")
    r = _run(tmp_path, {"session_id": "s2", "transcript_path": str(tp)})
    assert json.loads(r.stdout)["decision"] == "block"


def test_hook_clean_reply_passes(tmp_path):
    tp = _transcript(tmp_path, "PR #738, the ids-carry-names hook, is up")
    r = _run(tmp_path, {"session_id": "s3", "transcript_path": str(tp)})
    assert r.stdout.strip() == ""


def test_hook_fails_open(tmp_path):
    assert _run(tmp_path, {}).stdout.strip() == ""
    r = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == ""
    assert _run(tmp_path, {"transcript_path": "/nonexistent"}).stdout.strip() == ""


def test_unwritable_marker_fails_open(tmp_path):
    # If "once per turn" cannot be recorded, the guard must not block at all.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    tp = _transcript(tmp_path, "myrepo PR #738 is up")
    env = {**os.environ, "STOP_GUARD_STATE_DIR": str(blocker)}
    r = subprocess.run([sys.executable, str(HOOK)],
                       input=json.dumps({"session_id": "s4", "transcript_path": str(tp)}),
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and r.stdout.strip() == ""
