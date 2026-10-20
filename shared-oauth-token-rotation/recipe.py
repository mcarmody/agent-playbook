# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
recipe.py — reference implementation of the detect/probe/pause-and-reprobe
state machine for a shared on-disk OAuth credentials file that can rotate
out from under sibling processes (see SKILL.md).

This is deliberately harness-agnostic: the actual "does the token work"
check (`probe_fn`) is injected, not baked in — swap in whatever your
harness uses to make one cheap authenticated call (e.g. a minimal
`claude -p "reply with exactly: ok"` subprocess call on Claude Code).

Run directly: `uv run recipe.py --demo` walks the state machine through a
scripted sequence (auth failure -> recovered, then auth failure ->
needs_login -> paused -> re-probe -> resume) using a fake probe function,
so it's runnable and inspectable with no live credentials or network
access required.

No dependencies beyond the stdlib.
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Callable

AUTH_FAILURE_MARKERS = (
    "oauth access token has been revoked",
    "please run /login",
    "invalid authentication credentials",
    "failed to authenticate",
    "authentication_error",
    "401 unauthorized",
)


def is_auth_failure_signature(response: str) -> bool:
    """True if a turn failed because the shared OAuth token is dead
    (revoked/expired/invalid) rather than because context overflowed —
    the two produce the same zero-token-both-ways fingerprint otherwise,
    so this check has to key on the response body, not just "empty turn"."""
    r = (response or "").lower()
    return any(m in r for m in AUTH_FAILURE_MARKERS)


class AuthGuard:
    """detect -> self-heal the recoverable case -> escalate ONCE -> pause
    with periodic re-probe -> resume automatically when the token returns.

        guard = AuthGuard("myagent", probe_fn=my_probe)
        if is_auth_failure_signature(response):
            if guard.diagnose() == "recovered":
                respawn_or_retry()
            else:
                guard.enter_paused()
                if guard.should_alert():
                    alert_a_human()
        # on some idle tick:
        if guard.paused and guard.due_for_recheck() and guard.recheck():
            respawn_or_retry()
    """

    RECHECK_SEC = 120
    SELFHEAL_COOLDOWN_SEC = 180

    def __init__(self, name: str, probe_fn: Callable[[], bool], clock: Callable[[], float] = time.time):
        self.name = name
        self._probe = probe_fn
        self._clock = clock
        self.paused = False
        self._next_probe = 0.0
        self._last_selfheal = float("-inf")
        self._alerted = False

    def diagnose(self) -> str:
        if self._clock() - self._last_selfheal < self.SELFHEAL_COOLDOWN_SEC:
            return "needs_login"
        if self._probe():
            self._last_selfheal = self._clock()
            return "recovered"
        return "needs_login"

    def enter_paused(self) -> None:
        self.paused = True
        self._next_probe = self._clock() + self.RECHECK_SEC

    def should_alert(self) -> bool:
        if self._alerted:
            return False
        self._alerted = True
        return True

    def due_for_recheck(self) -> bool:
        return self.paused and self._clock() >= self._next_probe

    def recheck(self) -> bool:
        self._next_probe = self._clock() + self.RECHECK_SEC
        if self._probe():
            self.paused = False
            self._alerted = False
            self._last_selfheal = float("-inf")
            return True
        return False


def _demo() -> None:
    print("--- fingerprint check ---")
    assert is_auth_failure_signature("Please run /login · API Error: 401 OAuth access token has been revoked.")
    assert not is_auth_failure_signature("Prompt is too long")
    assert not is_auth_failure_signature("Sure — here is the summary you asked for.")
    print("ok: auth signature distinguishes revoked-token from overflow/normal")

    print("\n--- scenario 1: a sibling's refresh already fixed it ---")
    fake_clock = [0.0]
    guard = AuthGuard("demo", probe_fn=lambda: True, clock=lambda: fake_clock[0])
    result = guard.diagnose()
    print(f"diagnose() -> {result!r}")
    assert result == "recovered"

    print("\n--- scenario 2: the file is genuinely dead, then comes back ---")
    probe_state = {"ok": False}
    guard2 = AuthGuard("demo2", probe_fn=lambda: probe_state["ok"], clock=lambda: fake_clock[0])
    result = guard2.diagnose()
    print(f"diagnose() -> {result!r} (probe failing)")
    assert result == "needs_login"
    guard2.enter_paused()
    print(f"paused={guard2.paused}, should_alert()={guard2.should_alert()}, "
          f"should_alert() again={guard2.should_alert()} (only fires once)")
    fake_clock[0] += 60
    print(f"at +60s, due_for_recheck()={guard2.due_for_recheck()} (not yet, needs 120s)")
    fake_clock[0] += 61
    print(f"at +121s, due_for_recheck()={guard2.due_for_recheck()}")
    assert guard2.due_for_recheck()
    print("recheck() while still dead ->", guard2.recheck())
    probe_state["ok"] = True
    fake_clock[0] += 121
    print("token restored server-side; recheck() ->", guard2.recheck())
    assert not guard2.paused
    print("guard resumed automatically once the on-disk file authenticated again")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true", help="run the scripted demo")
    args = ap.parse_args()
    if args.demo:
        _demo()
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
