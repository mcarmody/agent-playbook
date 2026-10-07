"""Shared plumbing for Stop-hook guards. Stdlib only.

Three properties every guard built on this gets for free:

  (a) Block at most once per turn. A marker file keyed on (session, hash of
      the turn's user message) records that this guard already blocked; a
      redraft that still trips the check is let through, so a guard can never
      loop. If the marker cannot be written, the guard does not block at all
      (it cannot promise "once"). `stop_hook_active` is deliberately not
      used here: it is also true after a DIFFERENT hook blocked the same
      stop, which would silently disable this guard.
  (b) Fail open. Any exception, bad payload, missing transcript or I/O error
      means exit 0 with no output: the turn ends. A guard bug must never be
      the reason a turn cannot end.
  (c) Skip false-positive zones - that part is per guard (see
      stop_ids_carry_names.prose_only).

Claude Code Stop hook contract: the command gets a JSON payload on stdin
(`session_id`, `transcript_path`, `stop_hook_active`, ...). Printing
{"decision": "block", "reason": "..."} on stdout keeps the model working and
feeds `reason` back to it. No output / exit 0 lets the stop proceed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional


def state_dir() -> Path:
    return Path(os.environ.get("STOP_GUARD_STATE_DIR")
                or Path(tempfile.gettempdir()) / "stop-guards")


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name or "nosession")[:80]


def _marker(guard: str, session_id: str) -> Path:
    return state_dir() / guard / f"{_safe(session_id)}.json"


def turn_key(user_msg: str) -> str:
    return hashlib.sha1(user_msg.encode("utf-8", "replace")).hexdigest()


def already_blocked(guard: str, session_id: str, key: str) -> bool:
    try:
        return json.loads(_marker(guard, session_id).read_text()).get("turn_key") == key
    except Exception:
        return False


def record_block(guard: str, session_id: str, key: str) -> bool:
    """Write the per-turn marker. False means it could not be written."""
    try:
        p = _marker(guard, session_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"turn_key": key}))
        return True
    except Exception:
        return False


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def read_last_turn(transcript_path: str) -> tuple[str, str]:
    """(drafted_reply, user_msg) for the last turn of a Claude Code JSONL
    transcript. The turn starts at the last user event that carries text (a
    user event holding only tool_result blocks is not a new turn); the reply
    is all assistant text after it."""
    events = []
    for line in Path(transcript_path).read_text().splitlines():
        try:
            events.append(json.loads(line))
        except Exception:
            continue  # a half-flushed last line must not break the hook
    start = -1
    user_msg = ""
    for i, ev in enumerate(events):
        if ev.get("type") == "user":
            text = _text_of((ev.get("message") or {}).get("content"))
            if text.strip():
                start, user_msg = i, text
    reply = "\n".join(
        _text_of((ev.get("message") or {}).get("content"))
        for ev in events[start + 1:] if ev.get("type") == "assistant"
    ).strip()
    return reply, user_msg


def run_guard(name: str, decide: Callable[[str, dict], Optional[str]], *, off_env: str = "",
              stdin=None, out=None) -> int:
    """Run one guard end to end. `decide(reply, payload)` returns a block reason or
    None. Always returns 0: blocking is signalled on stdout, never by exit
    code, and every failure path falls through to 0."""
    stdin = stdin or sys.stdin
    out = out or sys.stdout
    try:
        payload = json.load(stdin)
        if off_env and os.environ.get(off_env) == "1":
            return 0
        tp = payload.get("transcript_path") or ""
        if not tp or not Path(tp).exists():
            return 0
        reply, user_msg = read_last_turn(tp)
        if not reply:
            return 0
        reason = decide(reply, payload)
        if not reason:
            return 0
        sid = str(payload.get("session_id") or "")
        key = turn_key(user_msg)
        if already_blocked(name, sid, key):
            return 0  # blocked once this turn already: let the redraft through
        if not record_block(name, sid, key):
            return 0  # cannot guarantee once-only: fail open
        print(json.dumps({"decision": "block", "reason": reason}), file=out, flush=True)
    except Exception as e:  # noqa: BLE001 - never wedge a turn on a hook bug
        print(f"{name}: failing open ({type(e).__name__}: {e})", file=sys.stderr, flush=True)
    return 0
