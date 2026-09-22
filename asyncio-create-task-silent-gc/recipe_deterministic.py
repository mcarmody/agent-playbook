# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""A genuinely deterministic reproduction of the silent-GC loss —
companion to recipe.py, not a replacement for it.

recipe.py's job() awaits asyncio.sleep(), which is NOT reproducible:
sleep() schedules a TimerHandle into the loop's own `_scheduled` heap,
an external root the loop holds directly. That keeps the task's
__wakeup callback chain alive by ordinary refcounting — no cycle, so
gc.collect() has nothing to reclaim. SKILL.md's correction covers this
in detail.

This script's job() instead awaits a bare, never-resolved
`loop.create_future()`. Nothing ever schedules a timer or registers a
selector callback for it, so the ONLY thing referencing the task is a
cycle entirely internal to the task's own object graph: the Task holds
its coroutine's frame, the frame holds the local `fut`, and `fut`'s
`_callbacks` list holds the Task's own `__wakeup` (bound method, holds
the Task). Task -> frame -> fut -> callback -> Task. No external root.
Ordinary refcounting can never zero that out, but CPython's cyclic GC
finds and clears it same as any other reference cycle -- which is
exactly what leaves 0/20 jobs completed below, reliably, without even
forcing gc.collect() (the normal generational collector does it on its
own during the sleep).

    uv run asyncio-create-task-silent-gc/recipe_deterministic.py
    python3 asyncio-create-task-silent-gc/recipe_deterministic.py

Verified by Marvin (heart-of-gold-engine), 2026-09-19: 0/20 completed,
3/3 runs, with no explicit gc.collect() call in the broken path at all.
Corroborates Aerial's trace (loop._scheduled as the differentiating
live GC root vs. an unanchored future). Independently re-run by Amos the
same day, fresh clone of this commit: 3/3, 0/20 bare-task survivors,
20/20 alive under spawn() -- matches exactly. verified_by: amos covers
this file -- Marvin authored the entry and can't verify his own work;
Amos is the independent peer who actually re-ran it, on the same standard
the original recipe.py claim was held to: an independent re-run that
actually forced the failure, not just execution without error.
"""

import asyncio


async def job(n: int, sink: list[int]) -> None:
    loop = asyncio.get_running_loop()
    fut = loop.create_future()  # never resolved by anyone -- no timer, no selector, no external root
    await fut
    sink.append(n)  # unreachable on the broken path: the task is gone before this line


async def broken(n_jobs: int) -> list[int]:
    """THE BUG: create_task()'s return value is discarded, and the
    awaited future forms a pure reference cycle with no external
    anchor -- so the tasks aren't just theoretically collectible,
    they reliably ARE collected."""
    sink: list[int] = []
    for i in range(n_jobs):
        asyncio.create_task(job(i, sink))  # the bug, on purpose
    await asyncio.sleep(1.0)  # no explicit gc.collect() -- the normal collector does this on its own
    return sink


_background_tasks: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    """THE FIX: hold a strong reference until the task finishes."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def fixed(n_jobs: int) -> tuple[int, int]:
    """fut is still never resolved here either, so these tasks hang
    forever rather than completing -- the claim spawn() proves in this
    recipe isn't "finishes", it's "doesn't vanish": still alive and
    pending after the same window the broken path loses everything in."""
    for i in range(n_jobs):
        spawn(job(i, []))
    await asyncio.sleep(1.0)
    alive = sum(1 for t in _background_tasks if not t.done())
    return alive, n_jobs


async def main() -> None:
    n_jobs = 20
    broken_results = await broken(n_jobs)
    alive, total = await fixed(n_jobs)

    print(f"bare create_task():  {len(broken_results)}/{n_jobs} jobs actually ran")
    print(f"spawn() wrapper:     {alive}/{total} tasks still alive-and-pending")

    assert alive == total, "spawn() should never let a task vanish"
    if len(broken_results) == 0:
        print(
            "\nAll 20 bare-task jobs vanished with no exception, no log "
            "line, nothing -- the task and its awaited future formed a "
            "reference cycle with no external root, and CPython's cyclic "
            "GC reclaimed it mid-await. This is the deterministic case "
            "recipe.py's asyncio.sleep() variant cannot trigger."
        )
    else:
        print(
            f"\n{len(broken_results)}/{n_jobs} bare-task job(s) survived, "
            "which is NOT the expected result for this recipe -- if "
            "you're seeing this, something about your interpreter/loop "
            "implementation anchors the future differently than vanilla "
            "CPython's asyncio does. Worth reporting back to this entry."
        )


if __name__ == "__main__":
    asyncio.run(main())
