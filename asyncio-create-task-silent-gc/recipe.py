# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Illustrates the asyncio.create_task() silent-GC pattern and its fix —
NOT a live reproduction of the loss. See SKILL.md's correction: under
vanilla CPython, a task awaiting a real primitive (asyncio.sleep, a
Future) stays reference-reachable via the loop's own scheduling structures
the whole time, so this script currently completes 20/20 on BOTH the bare
and spawn() paths, run after run (verified 2500+ trials, 0 losses).

    uv run asyncio-create-task-silent-gc/recipe.py
    python3 asyncio-create-task-silent-gc/recipe.py

Two runs over the same 20 fire-and-forget jobs: one with a bare
`asyncio.create_task(...)` call whose return value is discarded, one
routed through `spawn()`, which stashes the task in a module-level set and
drops it via `add_done_callback` once it finishes. The real-world bug this
documents is genuine (see SKILL.md's pr_evidence) and the fix (spawn())
is correct regardless — but this synthetic script does not force the
narrow race window (before a task's first `await` registers any callback)
that would make the loss happen on demand.
"""

import asyncio
import gc


async def job(n: int, sink: list[int]) -> None:
    await asyncio.sleep(0.01)
    sink.append(n)


async def broken(n_jobs: int) -> list[int]:
    """THE BUG: create_task()'s return value is discarded."""
    sink: list[int] = []
    for i in range(n_jobs):
        asyncio.create_task(job(i, sink))  # the bug, on purpose — no reference kept
    gc.collect()
    await asyncio.sleep(0.2)
    return sink


_background_tasks: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    """THE FIX: hold a strong reference until the task finishes."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def fixed(n_jobs: int) -> list[int]:
    sink: list[int] = []
    for i in range(n_jobs):
        spawn(job(i, sink))
    gc.collect()
    await asyncio.sleep(0.2)
    return sink


async def main() -> None:
    n_jobs = 20
    broken_results = await broken(n_jobs)
    fixed_results = await fixed(n_jobs)

    print(f"bare create_task():  {len(broken_results)}/{n_jobs} jobs actually ran")
    print(f"spawn() wrapper:     {len(fixed_results)}/{n_jobs} jobs actually ran")

    assert len(fixed_results) == n_jobs, "spawn() should never lose a task"
    if len(broken_results) == n_jobs:
        print(
            "\nExpected on vanilla CPython: job() awaits a real primitive "
            "(asyncio.sleep), which keeps the task reference-reachable via "
            "the loop's own scheduling structures the whole time, so this "
            "recipe does not currently trigger the loss (see SKILL.md's "
            "correction). The real incident it documents (pr_evidence) is "
            "genuine; spawn() is still the correct fix regardless."
        )
    else:
        print(
            f"\n{n_jobs - len(broken_results)} bare-task job(s) vanished "
            "with no exception, no log line, nothing. That's the bug — "
            "if you're seeing this, you've found conditions that trigger "
            "it; worth reporting back to this entry."
        )


if __name__ == "__main__":
    asyncio.run(main())
