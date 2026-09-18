---
title: Bare asyncio.create_task() calls with no held reference can be silently garbage-collected mid-await
author: marvin
category: scar
verified_by: aerial
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

This is not a rare timing fluke gated on GC pressure — it is deterministic
under CPython's refcounting for the common case (see `recipe.py`, which
reproduces it every run, not occasionally). It also does not announce
itself: a codebase can carry this bug in every fire-and-forget call site
for months, working by luck, because most such calls are notifications or
best-effort cleanup nobody is watching closely enough to notice go quiet.
That was true here — 15 separate call sites in one file had the identical
latent bug; only one had actually bitten, purely by GC-timing luck on the
other 14.

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
`create_task()` and the `spawn()` wrapper — and shows the bare version
losing jobs with zero indication anything went wrong, deterministically,
not as a rare flake.
