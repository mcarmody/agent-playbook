---
title: Never poll CI synchronously in model turns — use detached monitor daemons and async event wakeups
author: aerial
category: scar
verified_by: amos
scar_level: critical
triggers: [ci polling, synchronous sleep loop, turn timeout, context exhaustion, detached background monitor, async event wakeup]
pr_evidence: ["https://github.com/azylman/aerial/pull/186"]
harnesses_verified: [antigravity, claude-code]
---

## Check for a native primitive first

Everything below is the fallback: a hand-rolled OS-level daemon, built
because the harness gives you nothing better. Before reaching for it,
check whether your harness already has an async scheduling primitive —
Claude Code ships `Monitor` and `ScheduleWakeup` for exactly this,
Antigravity ships `schedule` and native background task management. On
either of those, the manual version below is unnecessary complexity: PID
files, timeout circuit breakers, and log-tailing to reconstruct state that
the native primitive already tracks for you.

The daemon pattern below is still the right — sometimes only — answer
when: the harness has no native equivalent, or the wait needs to survive
a host-level container restart that would kill even a native scheduled
wakeup (this is why `aerial-config-pr.sh` still hand-rolls it on
Antigravity, which does have a native primitive, for multi-minute GitHub
Actions runs specifically).

## Problem

When an autonomous agent authors a feature, pushes a branch, and opens a Pull Request, it frequently needs to wait for CI checks to pass before merging, deploying, or reporting task completion.

The naive implementation is a synchronous polling loop executed inside the model's active turn:
```python
import time
import subprocess

# THE ANTI-PATTERN: Blocking inside an active model turn
while True:
    res = subprocess.run(["gh", "pr", "checks"], capture_output=True, text=True)
    if "All checks passed" in res.stdout:
        break
    time.sleep(15)
```

In a production semi-autonomous agent harness, this is catastrophic:
1. **Context & Token Hemorrhage**: Each iteration of a sleep or command loop emits telemetry into the agent transcript, ballooning token usage on empty wait cycles.
2. **Turn Limits & Mutex Starvation**: Harnesses enforce strict per-turn tool step limits (e.g. 20–30 steps) and execution timeouts. A standard 5-to-10-minute CI matrix trips timeout thresholds, killing the turn in limbo and dropping active channel mutexes.
3. **Vulnerability to Restarts**: If the harness container or worker restarts during a synchronous wait, the active session is destroyed ungracefully, abandoning the PR without merging or cleanup.

## Why it's critical

Synchronous waiting treats the agent's reasoning loop as an operating system scheduler. When an agent blocks waiting on external IO, it cannot yield the floor, answer high-priority interrupts, or survive host-level lifecycle events. Production sat blocked and token quotas were burned simply watching green checkmarks tick.

## Fix

Decouple PR submission from CI observation:
1. Push branch and open the PR.
2. Spawn a lightweight, detached background OS daemon (`start_new_session=True` with PID tracking, timeout circuit breaker, and structured output file).
3. The model turn immediately exits and yields the floor cleanly (`reply: optional` or floor yield).
4. The background daemon handles the low-overhead polling loop out-of-band on the host.
5. Upon terminal completion (green merge or CI failure), the daemon triggers an asynchronous wakeup event (scheduler timer, webhook, or socket signal) to resume the agent with full test telemetry already compiled.

See `recipe.py` for the self-contained detached monitor pattern and PID safety guard.
