---
title: Assert a monotonic fencing token - holding a lock is not enough to reject a stale worker
author: amos
category: scar
verified_by: null
scar_level: silent
triggers: [distributed lock, mutex, task queue, worker restart, stale lock, fencing token, duplicate processing, lease expired, replayed delivery, compare-and-swap mismatch, interleaved duplicate turns]
pr_evidence: []
harnesses_verified: [claude-code]
---

## Problem

A worker takes a lock on a task, then pauses or restarts. Its lock expires and
a second worker takes the task. The first worker revives still believing it
holds the task and writes. A check that asks "is a lock held for this task?"
answers yes, because the second worker holds one. The stale write is accepted
and two workers process interleaved duplicates until something else, such as a
compare-and-swap mismatch, crashes one of them.

Lock presence cannot tell the current holder from a former one. The store
needs a value that orders holders.

## Pattern

Every lock acquisition returns a fencing token, a number that is strictly
greater than every token issued before it. The worker sends its token with
every state-changing request. The store keeps a watermark, the highest token it
has accepted, and applies this rule:

- **Accept** a request whose token is greater than or equal to the watermark,
  and raise the watermark to that token.
- **Reject** a request whose token is below the watermark. Reject it hard, with
  an error the caller cannot mistake for a retryable failure.

Then probe it. Do not trust that the rule is wired in because the code has a
token field:

- **Replay a stale token.** Acquire as worker A, acquire as worker B, then
  write as A. Assert the write is refused and the stored value is unchanged.
- **Positive control.** Write as B through the same path and assert it
  succeeds. If both writes fail, the harness is broken, not the fence.
- **Keep a broken-fence fixture.** Run the same probe against a store that
  checks only that some lock exists. The probe must fail there. This is the
  guard-not-flag rule from `probe-the-guard-not-the-flag` applied to a lock.

Two cautions. The token must be issued by the same store that checks it, or the
order is not shared. A wall-clock timestamp is not a token, because clocks on
two workers disagree.

## Run it

`recipe.py` builds a store with two checks: lock presence only, and the
monotonic fence. A stale worker is replayed against both.

```bash
uv run assert-monotonic-fencing-token/recipe.py
python3 assert-monotonic-fencing-token/recipe.py
```

Expected: the lock-presence store accepts the stale write and the fenced store
refuses it. The script exits non-zero if either result is reversed.

## Related

- `probe-the-guard-not-the-flag`: attempt the forbidden action and assert the
  refusal, which is the shape of the stale-token probe.
