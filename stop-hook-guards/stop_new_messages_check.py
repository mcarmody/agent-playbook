#!/usr/bin/env python3
"""Stop hook: hand over messages that landed while the agent was drafting.

In a multi-agent chat the agent claims a message, thinks for a minute, and
drafts a reply - while other participants keep posting. If the channel has
moved since the claim, this hook blocks the stop ONCE and returns the new
messages under one plain label. No topic matching, no echoing the draft back,
no instructions about what to do: the model decides what the messages mean,
as it would with any other new context.

Design points:
  * Pluggable fetch. How you read "messages newer than X" is chat-platform
    specific, so it is one function, `fetch_newer_messages(since_id, payload)`,
    returning [{"id", "author", "content"}, ...] oldest first. The default is
    a stub that returns nothing (guard inert). Point STALE_CHECK_FETCH at your
    own as "module:function" (module importable from this directory or
    PYTHONPATH), or edit the stub.
  * Baseline. `since_id` is the newest message id the agent had seen when the
    turn began. Whatever dispatches the turn writes it to
    <state dir>/baseline/<session_id> (one line). No baseline file means this
    is not a chat-driven turn: the hook does nothing.
  * Own posts are filtered by SELF_AUTHOR (env, optional): they are not "new"
    to the agent.
  * Once per turn via guard_lib's marker, plus `stop_hook_active`; fails open
    on every error, including the fetch raising or timing out.

Tests: test_stop_new_messages_check.py.   Kill switch: STALE_CHECK_OFF=1.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard_lib  # noqa: E402

LABEL = "These are new messages posted while you were typing and thinking and working:"


def fetch_newer_messages(since_id: str, payload: dict) -> list:
    """STUB. Return messages posted after `since_id`, oldest first, as dicts
    with "id", "author" and "content". Replace with a call to your chat API
    (or a local log of the channel). Raise freely: the hook fails open."""
    return []


def _fetcher():
    spec = os.environ.get("STALE_CHECK_FETCH", "")
    if not spec:
        return fetch_newer_messages
    mod, _, fn = spec.partition(":")
    return getattr(importlib.import_module(mod), fn or "fetch_newer_messages")


def read_baseline(session_id: str) -> str:
    try:
        p = guard_lib.state_dir() / "baseline" / guard_lib._safe(session_id)
        return p.read_text().strip()
    except Exception:
        return ""


def build_reason(messages: list) -> str:
    lines = [LABEL, ""]
    for m in messages:
        lines.append(f"{m.get('author') or 'unknown'}: {m.get('content', '')}")
    return "\n".join(lines)


def decide(reply: str, payload: dict):
    if payload.get("stop_hook_active"):
        return None  # already bounced once for this stop
    since = read_baseline(str(payload.get("session_id") or ""))
    if not since:
        return None  # not a chat-driven turn
    me = os.environ.get("SELF_AUTHOR", "")
    newer = [m for m in (_fetcher()(since, payload) or [])
             if str(m.get("id")) != since and not (me and str(m.get("author")) == me)]
    return build_reason(newer) if newer else None


if __name__ == "__main__":
    sys.exit(guard_lib.run_guard("new-messages", decide, off_env="STALE_CHECK_OFF"))
