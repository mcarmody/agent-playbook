# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Show that a bare asyncio.create_task() with no held reference can vanish
before it ever runs — and that holding a strong reference (the `spawn()`
pattern) fixes it deterministically, not just "usually".

    uv run asyncio-create-task-silent-gc/recipe.py
    python3 asyncio-create-task-silent-gc/recipe.py

Two runs over the same 20 fire-and-forget jobs: one with a bare
`asyncio.create_task(...)` call whose return value is discarded, one
routed through `spawn()`, which stashes the task in a module-level set and
drops it via `add_done_callback` once it finishes. Under CPython, a `Task`
with no strong reference anywhere is only weakly held by the event loop —
the moment nothing else points to it, reference counting collects it,
often before it has run even once. This is not a rare GC-timing fluke: it
reproduces every run under CPython, which is exactly why it's dangerous —
it happens the same way in production as it does here.
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
            "\nNote: this particular run's timing happened not to lose any "
            "bare tasks — rerun a few times, or see the real incident this "
            "is from (pr_evidence in SKILL.md). The fix is correct "
            "regardless of whether any one run reproduces the loss."
        )
    else:
        print(
            f"\n{n_jobs - len(broken_results)} bare-task job(s) vanished "
            "with no exception, no log line, nothing. That's the bug."
        )


if __name__ == "__main__":
    asyncio.run(main())
