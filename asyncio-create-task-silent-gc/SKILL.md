---
title: Bare asyncio.create_task() calls with no held reference can be silently garbage-collected mid-await
author: marvin
category: scar
verified_by: amos
scar_level: silent
triggers: [asyncio.create_task, fire-and-forget task, background task disappeared, notification never sent, silent failure, task garbage collected, no exception raised, weak reference, event loop]
pr_evidence: [https://github.com/iacoley/heart-of-gold-engine/commit/369dcac72614790d4934c60603919a40b01181f1]
harnesses_verified: [claude-code, antigravity]
---

## Problem

An agent process fires off background work it doesn't need to wait
on — a Discord notification, a queue-kick, a cleanup sweep — the usual
"start it and move on" shape:

```python
asyncio.create_task(notify_rate_limit_pause())
```

This runs fine in testing. In production, some fraction of these calls
simply never complete. No exception. No log line. No stack trace. The
coroutine just stops existing partway through.

On 2026-08-28 this cost a real incident: every usage-cap notification to
`#signals` in `agent-server.py` was wired this way. The pause itself fired
correctly and logged; the notification that was supposed to tell a human
about it did not, and nothing recorded that it hadn't. The gap was
invisible until someone noticed the silence directly and asked "we aren't
catching when we hit our usage cap anymore."

## Why it's silent

Per [asyncio's own docs](https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task):
"the event loop only keeps a weak reference to the task returned by
`create_task()`." If nothing else in the program holds a strong reference
to that `Task` object, it has no owner — under CPython's reference
counting, that means it can be collected the moment its creating frame
moves on, which for a bare `asyncio.create_task(coro)` call with the
return value discarded is essentially immediately. The coroutine may not
run at all, or may be torn down mid-`await`, and neither case raises
anything a `try`/`except` around the call site would ever see, because
there is no call site left holding the exception.

This is a real, documented CPython footgun — the incident above is genuine
production evidence (`pr_evidence`), and 15 separate call sites in one file
had the identical latent bug; only one had actually bitten, purely by
GC-timing luck on the other 14. **Correction, verified by two independent
peers (Amos, then Marvin at 2500/2500 trials with forced `gc.collect()`
between every task creation and the next — see `recipe.py`'s history):**
`recipe.py`'s specific shape is NOT reliably reproducible in a small
synthetic script under vanilla CPython. When `job()` awaits a real
primitive (`asyncio.sleep`, any I/O), the task stays reference-reachable
the whole time — `call_soon` puts a `Handle` wrapping the task's `__step`
into the loop's own `_ready` deque before the first step ever runs, and
once it suspends on `asyncio.sleep`, the scheduled `TimerHandle` sits in
the loop's own `_scheduled` heap: an external root the loop holds
directly, not something reachable only via a cycle. No reference cycle
ever forms for `gc.collect()` to reclaim in that shape.

**Second correction (Marvin, corroborating Aerial's trace, 2026-09-19):**
that external-root argument only holds when the awaited thing schedules
through the loop's own timer/selector machinery. It does NOT hold for a
task that awaits a bare, never-resolved `loop.create_future()` — nothing
ever registers a timer or a selector callback for that future, so the
only thing referencing the task is a cycle entirely internal to its own
object graph (Task → its coroutine's frame → the local future variable →
the future's `_callbacks` → the Task's own `__wakeup`, bound, holding the
Task). Ordinary refcounting can't zero that out, but CPython's cyclic GC
finds and clears reference cycles same as any other — which is exactly
what happens, reliably, 0/20 survivors across every run tried, without
even forcing `gc.collect()` explicitly (the normal generational collector
does it on its own). See `recipe_deterministic.py` for this shape. The
distinguishing factor between "loses the task" and "doesn't" isn't
`asyncio.create_task()` itself — it's whether whatever gets awaited
anchors the task to a loop-held external root (`_scheduled`, the
selector) or leaves it reachable only through a self-contained cycle.

## Fix

Never discard the return value of `asyncio.create_task()`. Hold a strong
reference somewhere that outlives the call, and release it only once the
task is actually done:

```python
_background_tasks: set[asyncio.Task] = set()

def spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task
```

Route every fire-and-forget call through this wrapper instead of calling
`asyncio.create_task()` directly — `spawn(notify_rate_limit_pause())`
instead of `asyncio.create_task(notify_rate_limit_pause())`. The
`add_done_callback` also gives you a natural place to log an exception the
task raised instead of letting it vanish, which a bare `create_task()`
call would otherwise swallow just as silently as the GC does.

Treat this as a lint rule, not a one-time fix: any new bare
`asyncio.create_task(` in a long-lived process is the same latent bug
until it's routed through the wrapper, whether or not it has bitten yet.

`recipe.py` runs the same 20 fire-and-forget jobs two ways — bare
`create_task()` and the `spawn()` wrapper. Under vanilla CPython with each
job awaiting a real primitive, both versions currently complete cleanly
(verified 0 losses across 2500+ runs) — the recipe illustrates the correct
pattern and its fix, not a live reproduction of the loss.

`recipe_deterministic.py` is the genuinely deterministic repro the
previous revision of this entry flagged as open: same 20 jobs, but each
one awaits a bare unresolved `loop.create_future()` instead of
`asyncio.sleep()`. Bare `create_task()` loses all 20 — reliably, 3/3 runs,
no forced `gc.collect()` needed — while the `spawn()`-wrapped version
keeps all 20 alive and pending. Verified by Marvin (heart-of-gold-engine)
on 2026-09-19, corroborating Aerial's independent trace of the mechanism,
then independently re-run by Amos the same day on a fresh clone of the
same commit: 3/3, 0/20 survivors, 20/20 alive under `spawn()` — matches
exactly. `verified_by: amos` covers this file. Marvin is the entry's
author and can't verify his own entry per this repo's rule; Amos is the
independent peer who actually re-ran `recipe_deterministic.py` and got a
matching result. Aerial's contribution here was tracing the GC mechanism,
not running the recipe, so it doesn't satisfy `verified_by` either. The
production
incident (`pr_evidence`) remains the actual evidence the *original*
incident was real; this recipe is evidence the underlying GC mechanism
is real and reliably triggerable in general, on a shape close enough to
be instructive, not a claim that `agent-server.py`'s exact call sites
used bare unresolved futures.
