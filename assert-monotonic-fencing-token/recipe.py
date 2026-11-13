# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Show that lock presence accepts a stale worker and a monotonic fence rejects it.

    uv run assert-monotonic-fencing-token/recipe.py
    python3 assert-monotonic-fencing-token/recipe.py

Worker A acquires the lock, then stalls. Its lease expires and worker B
acquires the lock. A revives and writes. Two stores are tried: one that only
checks that a lock is held, and one that also checks the fencing token against
a watermark.
"""

import sys


class StaleWrite(Exception):
    pass


class Store:
    """A single-value store behind a lock. `fenced` switches the token check on."""

    def __init__(self, fenced: bool):
        self.fenced = fenced
        self.value = "initial"
        self.lock_held = False
        self._next_token = 0
        self.watermark = 0

    def acquire(self) -> int:
        self._next_token += 1  # strictly increasing, issued by the same store that checks it
        self.lock_held = True
        return self._next_token

    def write(self, token: int, value: str) -> None:
        if not self.lock_held:
            raise StaleWrite("no lock held")
        if self.fenced:
            if token < self.watermark:
                raise StaleWrite(f"token {token} below watermark {self.watermark}")
            self.watermark = token
        self.value = value


def stale_worker_replay(store: Store) -> bool:
    """Return True if the store refused the stale write and kept the live value."""
    token_a = store.acquire()
    token_b = store.acquire()  # A's lease expired; B takes over

    store.write(token_b, "from-B")  # positive control: the current holder succeeds
    if store.value != "from-B":
        raise RuntimeError("harness broken: the current holder could not write")

    try:
        store.write(token_a, "from-A")  # the stale worker revives
    except StaleWrite:
        pass
    return store.value == "from-B"


def main() -> int:
    presence_only = stale_worker_replay(Store(fenced=False))
    fenced = stale_worker_replay(Store(fenced=True))
    print(f"lock-presence store refused the stale write: {presence_only}")
    print(f"fenced store refused the stale write: {fenced}")
    if presence_only or not fenced:
        print("UNEXPECTED: the probe did not separate the two stores")
        return 1
    print("The presence check accepted the stale worker; the monotonic fence refused it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
