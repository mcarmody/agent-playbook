# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Show why collapsing a deliberate rejection into the same bucket as
"server unreachable" turns a safe fallback into a silent override.

    uv run semantic-rejection-vs-transport-fallback/recipe.py
    python3 semantic-rejection-vs-transport-fallback/recipe.py

A minimal claim client against a fake API with three possible outcomes:
success (200), a deliberate conflict (409, someone else already holds the
resource), and a real transport failure (connection refused). The broken
client only ever sees "got a 2xx" or "didn't" and falls back to claiming
locally on either kind of "didn't" — correct for a dead server, silently
wrong for an explicit rejection. The fixed client raises a distinct
exception for the 409 case so the caller can tell the two apart.
"""

from __future__ import annotations


class Unreachable(Exception):
    """The server genuinely could not be reached. Falling back is correct."""


class Blocked(Exception):
    """The server responded and said no. Falling back overrides that no."""

    def __init__(self, holder: str):
        self.holder = holder
        super().__init__(f"blocked, held by {holder!r}")


def fake_api_call(scenario: str) -> dict:
    """Simulates the three outcomes a real HTTP client actually sees."""
    if scenario == "success":
        return {"status": 200, "ok": True}
    if scenario == "conflict":
        return {"status": 409, "ok": False, "blocked": True, "holder": "amos"}
    if scenario == "down":
        raise ConnectionError("connection refused")
    raise ValueError(scenario)


def broken_claim(scenario: str, local_name: str) -> str:
    """THE BUG: any non-2xx (including a real rejection) folds into the
    same 'must be unreachable, fall back to local' path."""
    try:
        resp = fake_api_call(scenario)
    except ConnectionError:
        resp = None
    if resp is None or resp.get("status") != 200:
        return local_name  # claims locally — wrong for a real 409
    return local_name


def fixed_claim(scenario: str, local_name: str) -> str:
    """THE FIX: a 409 with a real body raises Blocked, a transport error
    raises Unreachable — the caller can no longer confuse the two."""
    try:
        resp = fake_api_call(scenario)
    except ConnectionError as e:
        raise Unreachable(str(e)) from e
    if resp["status"] == 409 and resp.get("blocked"):
        raise Blocked(resp["holder"])
    return local_name


def main() -> None:
    print("broken client (collapses rejection and unreachable together):")
    for scenario in ("success", "conflict", "down"):
        result = broken_claim(scenario, local_name="marvin")
        print(f"  scenario={scenario!r:10} -> claims locally as {result!r}")

    print("\nfixed client (rejection and unreachable are distinct exceptions):")
    for scenario in ("success", "conflict", "down"):
        try:
            result = fixed_claim(scenario, local_name="marvin")
            print(f"  scenario={scenario!r:10} -> claims locally as {result!r}")
        except Blocked as e:
            print(f"  scenario={scenario!r:10} -> Blocked, held by {e.holder!r} (no local claim made)")
        except Unreachable as e:
            print(f"  scenario={scenario!r:10} -> Unreachable ({e}) — local claim is the correct fallback")

    print(
        "\nThe broken client claims locally as 'marvin' even for "
        "scenario='conflict', where amos actually holds it — a fabricated "
        "success silently overriding a real rejection. The fixed client "
        "tells the two apart and only falls back for a genuine outage."
    )


if __name__ == "__main__":
    main()
