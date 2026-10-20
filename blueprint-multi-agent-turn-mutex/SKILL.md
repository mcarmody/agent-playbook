---
title: Multi-agent coordination and turn mutexes — distributed semaphores, fencing tokens, and inbound message filtering
author: aerial
category: howto
verified_by: null
scar_level: none
triggers: [building an agent harness from scratch, multi-agent coordination, turn mutex, distributed semaphore, fencing token, split-brain turns, thundering herd chat, inbound message classification, fast-ack sla, banana protocol, handoff envelope, greenfield agent fleet]
pr_evidence: []
harnesses_verified: [antigravity]
---

## The problem this solves

When scaling from an isolated single-agent loop to a fleet of autonomous agents sharing a workspace or communication channel, three immediate operational pathologies occur:

1. **The Chat Thundering Herd**: A single user prompt or ambient event in a shared channel wakes up every listening agent simultaneously. All agents evaluate the prompt, burn inference tokens in parallel, and collide while attempting to post simultaneous replies or commit competing mutations.
2. **Split-Brain Execution & Zombie Turns**: An agent acquires a turn lock and begins executing a tool call or long inference step. If the tool blocks or network jitter causes the lock's heartbeat/lease to expire, a peer agent takes over the floor. When the original agent finally wakes up, it blindly executes stale writes against shared state, causing silent data corruption.
3. **Conversational Echo Loops**: Unstructured agent chatter triggers conversational ping-pong. Agent A posts an update, Agent B's relevance classifier considers it new context and replies, which triggers Agent A again, generating an unbounded, costly feedback loop.

Solving multi-agent collaboration cannot rely on polite prompt instructions alone. It requires deterministic engineering primitives at the harness transport layer: **inbound classification**, **distributed turn mutexes with monotonic fencing tokens**, and **machine-parseable handoff envelopes**.

## The three-piece architecture, as actually run in this household's fleet

This pattern reflects the operational architecture running in production across this household's multi-agent channels (`#the-banana-stand` via Banana Protocol v0.6.0 and `banana.client` turn-taking). Three decoupled layers manage execution:

### 1. 3-Tier Inbound Message Classification (Transport Layer Filter)

Before an inbound message or event ever touches an LLM context window, the transport harness classifies it into one of three strict tiers:

- **`DIRECT` (Explicit Summons)**: The message explicitly addresses the agent (via direct `@mention`, user DM, or targeted `to: <agent>` field in a protocol envelope).
  - *Action*: Mandatory model invocation. Triggers a strict Fast-ACK SLA (e.g. 2-minute deadline).
- **`CLASSIFIED` (Ambient Floor Contribution)**: The message is broadcast to the open room with no specific target (`to: null`).
  - *Action*: Evaluated against lightweight deterministic filters (regex, channel membership, active subject match) or a small, cheap classifier. The agent decides whether to claim the floor or yield.
- **`SILENT` (Suppressed Background / Sentinel)**: Heartbeats, cron notifications, internal tool output, or explicit `PASS` / silence sentinels.
  - *Action*: Recorded in session logs without invoking the model or broadcasting to the channel.

By pruning irrelevant chatter at the transport layer, agents avoid burning inference budget on ambient noise.

### 2. Distributed Turn Mutexes with Monotonic Fencing Tokens

To prevent thundering herds, agents must acquire an exclusive distributed lease before speaking or mutating shared resources:

```python
def acquire_turn_lease(subject: str, agent_id: str, ttl_seconds: float = 30.0):
    """Attempt atomic turn claim and return lease with monotonic fencing token."""
    ...
```

Simple locks fail in distributed systems because an agent can pause (e.g., during long API generation or garbage collection) while its lock lease expires. If another agent acquires the lock, both agents now believe they hold the floor.

To eliminate split-brain mutations, the lock manager assigns a strictly increasing integer **fencing token** to every granted lease.
- When an agent commits a state change or broadcasts a turn result, it presents its fencing token.
- Any operation with a fencing token lower than the highest token processed by the resource is deterministically rejected as stale.

### 3. Structured Handoff Envelopes & Round Clamping (Banana Protocol)

Every external agent message concludes with a standardized, machine-readable JSON envelope:

```python
handoff_envelope = {
    "v": 1,
    "kind": "answer",  # answer, proposal, status, consensus, summary
    "reply": "optional",  # required, optional, none
    "floor": "open",  # open, closed
    "scope": "channel",
    "subject": "task-slug",
    "round": 2,
    "max_rounds": 10,
    "to": None,
    "sdk": "0.6.0",
}
```

Key invariants enforced by the envelope protocol:
- **Floor State (`open` vs `closed`)**: Setting `floor: "closed"` yields the floor permanently, signaling task conclusion and forbidding unsolicited follow-ups.
- **Round Clamping (`max_rounds: 10`)**: Every turn increments `round`. When `round >= max_rounds`, agents must close the floor (`floor: "closed"`, `reply: "none"`), clamping echo loops before runaway costs occur.
- **Fast-ACK SLA Targeting**: Setting `reply: "required"` mandates specifying a target agent (`to: "agent_name"`), establishing clear single-agent responsibility.

## How to apply it, greenfield

1. **Gate the LLM behind transport-level classification**: Never pass raw stream events directly into model prompts. Filter `DIRECT` vs `CLASSIFIED` vs `SILENT` at the gateway.
2. **Implement monotonic fencing tokens on every turn lock**: Use an atomic counter (Redis `INCR`, Postgres sequence, or Raft state machine) whenever granting a turn lease. Reject any downstream action carrying an outdated token.
3. **Enforce protocol envelope parsing in harness post-processing**: Automatically extract the trailing handoff block from agent output. Reject messages that violate protocol state (e.g., attempting to close the floor while marking `reply: "required"`).
4. **Cap conversation depth deterministically**: Hardcode an upper bound on round counts (e.g. 10 rounds) to guarantee termination even when agents fail to reach consensus.
