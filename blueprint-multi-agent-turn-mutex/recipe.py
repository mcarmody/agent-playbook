# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Demonstrate multi-agent coordination with turn mutexes, fencing tokens, and 3-tier inbound filtering.

Run:
    python3 blueprint-multi-agent-turn-mutex/recipe.py

Simulates:
1. Inbound message 3-tier transport classification (DIRECT, CLASSIFIED, SILENT).
2. Distributed mutex lease acquisition with monotonic fencing token generation.
3. Lock expiration and preemption scenario where a delayed agent attempts a stale commit.
4. Fencing token verification rejecting split-brain / stale writes while allowing valid commits.
5. Banana Protocol envelope validation and round clamping.
"""

from dataclasses import dataclass
import enum
import json
import time
from typing import Any, Dict, List, Optional


class MessageTier(enum.Enum):
    DIRECT = "direct"
    CLASSIFIED = "classified"
    SILENT = "silent"


@dataclass
class InboundMessage:
    sender: str
    content: str
    target: Optional[str] = None
    is_heartbeat: bool = False
    is_pass_sentinel: bool = False


class InboundClassifier:
    """Classifies incoming messages before LLM invocation."""

    @staticmethod
    def classify(msg: InboundMessage, agent_name: str) -> MessageTier:
        if msg.is_heartbeat or msg.is_pass_sentinel:
            return MessageTier.SILENT
        if msg.target == agent_name or f"@{agent_name}" in msg.content:
            return MessageTier.DIRECT
        return MessageTier.CLASSIFIED


@dataclass
class Lease:
    holder: str
    fencing_token: int
    subject: str
    expires_at: float

    def is_valid(self, now: float) -> bool:
        return now < self.expires_at


class TurnCoordinator:
    """Simulates an atomic distributed lock manager with monotonic fencing tokens."""

    def __init__(self) -> None:
        self._current_lease: Optional[Lease] = None
        self._token_counter: int = 0
        self._last_committed_token: int = 0

    def claim_turn(self, subject: str, agent: str, ttl_seconds: float = 0.5) -> Optional[Lease]:
        now = time.time()
        if self._current_lease and self._current_lease.is_valid(now):
            # Floor held by another agent
            if self._current_lease.holder != agent:
                return None

        self._token_counter += 1
        self._current_lease = Lease(
            holder=agent,
            fencing_token=self._token_counter,
            subject=subject,
            expires_at=now + ttl_seconds,
        )
        return self._current_lease

    def commit_turn(self, lease: Lease, payload: str) -> bool:
        """Commit an agent turn, validating that the fencing token is strictly monotonic."""
        now = time.time()
        # Reject if fencing token is stale (a higher token has already been committed or issued)
        if lease.fencing_token <= self._last_committed_token:
            return False

        # Reject if lease expired and another lease was issued
        if not lease.is_valid(now) and (self._current_lease and self._current_lease.fencing_token > lease.fencing_token):
            return False

        self._last_committed_token = lease.fencing_token
        return True


def format_handoff_envelope(
    kind: str,
    subject: str,
    current_round: int,
    max_rounds: int = 10,
    target: Optional[str] = None,
    floor: str = "open",
    reply: str = "optional",
) -> Dict[str, Any]:
    """Generates a Banana Protocol v0.6.0 compliant handoff dictionary."""
    if current_round >= max_rounds:
        floor = "closed"
        reply = "none"

    return {
        "v": 1,
        "kind": kind,
        "reply": reply,
        "floor": floor,
        "scope": "channel",
        "subject": subject,
        "round": current_round,
        "max_rounds": max_rounds,
        "to": target,
        "sdk": "0.6.0",
    }


def main() -> None:
    print("=== Step 1: 3-Tier Inbound Message Classification ===")
    classifier = InboundClassifier()
    agent_id = "aerial"

    m1 = InboundMessage(sender="alex", content="aerial review PR 14", target="aerial")
    m2 = InboundMessage(sender="amos", content="Desk open for fleet claims", target=None)
    m3 = InboundMessage(sender="system", content="PASS", is_pass_sentinel=True)

    t1 = classifier.classify(m1, agent_id)
    t2 = classifier.classify(m2, agent_id)
    t3 = classifier.classify(m3, agent_id)

    print(f"Direct summons -> {t1.value.upper()} (Invokes model with Fast-ACK SLA)")
    print(f"Ambient broadcast -> {t2.value.upper()} (Evaluates relevance filter before claiming)")
    print(f"Sentinel silence -> {t3.value.upper()} (Suppressed from invocation)")

    assert t1 == MessageTier.DIRECT
    assert t2 == MessageTier.CLASSIFIED
    assert t3 == MessageTier.SILENT

    print("\n=== Step 2: Distributed Turn Lease & Fencing Tokens ===")
    coord = TurnCoordinator()
    subject = "foundational-playbook-blueprints"

    # Agent 'aerial' claims floor with TTL 0.1s
    lease_aerial = coord.claim_turn(subject, "aerial", ttl_seconds=0.1)
    assert lease_aerial is not None
    print(f"Aerial acquired turn: token={lease_aerial.fencing_token}, expires_in=0.1s")

    # Peer 'zero' tries to claim immediately while lease is active -> rejected
    lease_zero_blocked = coord.claim_turn(subject, "zero")
    assert lease_zero_blocked is None
    print("Zero attempted claim while Aerial's lease active: REJECTED (Mutex locked)")

    # Simulate Aerial hitting a long inference/tool delay (0.15s sleep)
    print("Simulating Aerial encountering 150ms inference delay (lease expires)...")
    time.sleep(0.15)

    # Zero claims the turn after lease expiry
    lease_zero = coord.claim_turn(subject, "zero", ttl_seconds=0.5)
    assert lease_zero is not None
    print(f"Zero claimed expired turn: token={lease_zero.fencing_token}")
    assert lease_zero.fencing_token > lease_aerial.fencing_token

    # Zero commits turn successfully with token 2
    commit_zero = coord.commit_turn(lease_zero, "Zero finished scaffolding Track 1")
    print(f"Zero commit with token={lease_zero.fencing_token}: {'SUCCESS' if commit_zero else 'FAILED'}")
    assert commit_zero is True

    # Aerial finally wakes up and tries to commit with stale token 1 -> MUST BE REJECTED
    commit_aerial_stale = coord.commit_turn(lease_aerial, "Aerial late commit")
    print(f"Aerial wake-up commit with stale token={lease_aerial.fencing_token}: {'SUCCESS' if commit_aerial_stale else 'REJECTED (Stale Fencing Token)'}")
    assert commit_aerial_stale is False, "Stale fencing token commit must be rejected to prevent split-brain"

    print("\n=== Step 3: Handoff Envelope Formatting & Round Clamping ===")
    env_open = format_handoff_envelope(
        kind="proposal",
        subject=subject,
        current_round=2,
        max_rounds=10,
        floor="open",
        reply="optional",
    )
    print(f"Round 2 envelope: floor={env_open['floor']}, reply={env_open['reply']}")
    assert env_open["floor"] == "open"
    assert env_open["reply"] == "optional"

    env_clamped = format_handoff_envelope(
        kind="summary",
        subject=subject,
        current_round=10,
        max_rounds=10,
        floor="open",  # requested open, but should be clamped closed
        reply="required",
    )
    print(f"Round 10 envelope (clamped): floor={env_clamped['floor']}, reply={env_clamped['reply']}")
    assert env_clamped["floor"] == "closed"
    assert env_clamped["reply"] == "none"

    print("\nAll multi-agent coordination assertions verified clean! 🍌")


if __name__ == "__main__":
    main()
