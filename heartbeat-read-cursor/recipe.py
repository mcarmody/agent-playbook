# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Show a polling cursor losing a message after one failed read.

    uv run heartbeat-read-cursor/recipe.py
    python3 heartbeat-read-cursor/recipe.py

Three polling rounds against a fake channel. Round 2's read times out while
message 3 is waiting. The naive loop moves its cursor to "now" on every run,
so message 3 falls behind it. `poll` keeps the cursor and delivers it in
round 3.
"""

import sys


class Channel:
    def __init__(self):
        self.messages = [1, 2]
        self.fail_next = False

    def read(self, after=None, limit=25):
        if self.fail_next:
            self.fail_next = False
            raise TimeoutError("read timed out")
        if after is None:
            return self.messages[-limit:]
        return [m for m in self.messages if m > after][:limit]


def naive(ch, cursor):
    """Advances the cursor to the newest id on every run: the bug."""
    try:
        new = ch.read(after=cursor)
    except Exception:
        new = []                      # a failure reads as "quiet"
    return max(ch.messages), new


def poll(ch, cursor):
    try:
        new = ch.read(after=cursor)
    except Exception:
        return cursor, None           # unknown: retry from the same cursor
    if new:
        return new[-1], new           # advance only to a message we received
    try:
        ch.read(after=None, limit=1)  # prove the transport before trusting silence
    except Exception:
        return cursor, None
    return cursor, []


def run(step):
    ch, cursor, got = Channel(), 0, []
    for rnd in (1, 2, 3):
        if rnd == 2:
            ch.messages.append(3)
            ch.fail_next = True
        if rnd == 3:
            ch.messages.append(4)
        cursor, new = step(ch, cursor)
        got += new or []
    return got


def main() -> int:
    bad, good = run(naive), run(poll)
    print("naive loop delivered:", bad)
    print("poll() delivered:    ", good)
    assert bad == [1, 2, 4], bad      # message 3 lost
    assert good == [1, 2, 3, 4], good
    return 0


if __name__ == "__main__":
    sys.exit(main())
