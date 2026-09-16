# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Demonstrate anti-chatter sentinel filtering and PASS protocol for agent harnesses.

Run:
    uv run anti-chatter-silent-sentinels/recipe.py
    python3 anti-chatter-silent-sentinels/recipe.py

Validates:
1. PASS protocol suppresses transmission completely when an agent yields.
2. Intermediate play-by-play self-narration is stripped from intermediate turns.
3. Substantive answers, markdown links, and code blocks are preserved intact.
4. Error diagnostics, tracebacks, and non-zero exit forensics are NEVER swallowed.
"""

import re
from typing import Tuple

# Regex pattern matching conversational play-by-play filler
CHATTER_PATTERNS = [
    re.compile(r"^(?:I will now|Now I am going to|Let me|I'm going to|I am going to)\s+.*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(?:Searching for|Running command|Checking the|Inspecting the|Looking at)\s+.*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(?:Next, I will|First, I'll|I have initiated|I am now)\s+.*$", re.IGNORECASE | re.MULTILINE),
]

PASS_SENTINEL = re.compile(r"^\s*(?:PASS|NOOP|🍌\s*PASS)\s*$", re.IGNORECASE)


class AntiChatterFilter:
    @staticmethod
    def is_silent_sentinel(content: str) -> bool:
        """Returns True if the content is an explicit silence/pass sentinel."""
        return bool(PASS_SENTINEL.match(content.strip()))

    @staticmethod
    def filter_turn(content: str, is_terminal: bool = False, is_error: bool = False) -> Tuple[bool, str]:
        """Filter a turn payload.

        Returns (should_broadcast, cleaned_content).
        - If content is a silence sentinel, returns (False, "").
        - If is_error is True, passes through content untouched.
        - If is_terminal is True, preserves content but cleans leading chatter.
        - If intermediate turn, strips self-narration chatter.
        """
        # Invariant: Never swallow error diagnostics
        if is_error:
            return True, content

        # Invariant: PASS sentinel yields turn silently
        if AntiChatterFilter.is_silent_sentinel(content):
            return False, ""

        cleaned = content
        for pat in CHATTER_PATTERNS:
            cleaned = pat.sub("", cleaned)

        # Collapse excess empty lines introduced by filtering
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

        if not cleaned:
            return False, ""

        return True, cleaned


def run_tests() -> None:
    print("--- 1. Testing PASS Sentinel Suppression ---")
    pass_cases = ["PASS", "pass", "NOOP", "🍌 PASS", "  PASS  \n"]
    for text in pass_cases:
        broadcast, _ = AntiChatterFilter.filter_turn(text)
        assert not broadcast, f"Expected suppression for '{text}', but got broadcast"
    print("SUCCESS: Explicit PASS sentinels successfully suppressed from external broadcast.")

    print("\n--- 2. Testing Intermediate Self-Narration Filtering ---")
    noisy_intermediate = (
        "I will now search the codebase for the token cache.\n"
        "Searching for singleflight references...\n"
        "Let me inspect `cache.py` to see the existing implementation."
    )
    broadcast, cleaned = AntiChatterFilter.filter_turn(noisy_intermediate, is_terminal=False)
    assert not broadcast or not cleaned, f"Expected intermediate chatter to be completely stripped, got: {cleaned!r}"
    print("SUCCESS: Intermediate play-by-play chatter successfully stripped.")

    print("\n--- 3. Testing Substantive Output & Code Preservation ---")
    substantive = (
        "I will now run the test suite.\n\n"
        "Resolved the gateway race condition in `cache.py`:\n\n"
        "```python\ndef get_role():\n    return sf.do(key, fetch)\n```\n\n"
        "All 42 unit tests passed cleanly."
    )
    broadcast, cleaned = AntiChatterFilter.filter_turn(substantive, is_terminal=True)
    assert broadcast
    assert "def get_role():" in cleaned
    assert "All 42 unit tests passed cleanly." in cleaned
    assert "I will now run the test suite." not in cleaned
    print("SUCCESS: Substantive content and code blocks preserved while leading chatter removed.")

    print("\n--- 4. Testing Error Forensics Passthrough Invariant ---")
    failure_trace = (
        "Traceback (most recent call last):\n"
        "  File \"test_gateway.py\", line 42, in test_reconnect\n"
        "    assert response.status_code == 200\n"
        "AssertionError: Expected 200, got 429 Too Many Requests"
    )
    broadcast, cleaned = AntiChatterFilter.filter_turn(failure_trace, is_terminal=True, is_error=True)
    assert broadcast
    assert cleaned == failure_trace, "Error forensics MUST NEVER be modified or swallowed"
    print("SUCCESS: Error tracebacks and failure diagnostics passed through untouched.")


if __name__ == "__main__":
    run_tests()
