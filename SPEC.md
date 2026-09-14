# Agent practices commons — pattern package spec

Agreed in #agent-chat (Crab Cavern), 2026-09-13, by Amos, Zero and Aerial.
Standalone repo per Mike's explicit call — not folded into any one team's
product repo — so no agent has to grep past unrelated code, dependencies,
or CI to reach this. One canonical doc, code that enforces the format
rather than convention alone — same approach as
`specs/agent-handoff-envelope-v0.md` in the household workspace.

Priorities, in order: agent accessibility and legibility, ease of direct
reuse with minimal legwork, human legibility.

**Scope, per Mike, 2026-09-13: this is not a scar log.** A scar (a failure,
a silent bug, a gotcha) is one kind of entry, not the whole point. Basics
and affirmative tips belong equally — how to spawn a detached process at
all, what a PEP 723 header is for, how this fleet's handoff envelope
works, what "verified_by" even means. Write every entry assuming the
reader has no prior experience running an agent, a fleet, or a harness.
Nothing here is too basic to spell out; a step skipped as "obvious" is a
step a new reader has to go find somewhere else, which defeats the
directory's whole purpose. `category` (below) is how an entry declares
which kind it is — `scar` is not the default assumption.

## Layout

Everything below lives at the repo root — this repo *is* the pattern
directory, nothing else shares it.

```
/
  SPEC.md              this file
  README.md            generated: human-facing table (Pattern, Author,
                        Verified By, Direct Command)
  llms.txt              generated: compact index for agent ingestion
  manifest.json         generated: full frontmatter catalog for tag/dep
                        queries
  validate.py           CI check: frontmatter schema + fenced-block syntax
  generate_catalog.py   regenerates llms.txt/manifest.json/README.md
  <kebab-name>/
    SKILL.md            frontmatter + problem/solution doc
    recipe.py           optional: standalone PEP 723 script (`uv run
                         recipe.py`), hermetic, no pre-flight setup
    adapters/           optional: harness-specific shims
```

## `SKILL.md` frontmatter schema

```yaml
title: string, required
author: string, required
verified_by: string or null — the peer who executed the recipe and
  confirmed it works; null blocks merge, see Gate below
category: scar | tip | howto, required — scar: a failure and its fix.
  tip: an affirmative technique worth reusing, no failure required.
  howto: a basic, assume-no-experience walkthrough (spawning a process,
  reading a manifest, what a field in this repo's own schema means).
scar_level: none | silent | critical — required when category is scar,
  ignore (leave `none`) otherwise
triggers: list of strings — when an agent should pull this pattern
pr_evidence: list of URLs — may be empty for a tip or howto with no
  incident to cite
harnesses_verified: list of strings, optional, defaults to `[]` —
  which agent harness(es) (e.g. `claude-code`, `antigravity`) someone has
  actually run this recipe under. Empty means unclaimed: it may still
  work elsewhere, nobody's confirmed it there yet.
```

## Native primitive first, fallback second

Before writing a pattern that hand-rolls a capability — a detached
background process, a polling loop, a retry wrapper — check whether the
harness you're on already provides it natively. Several of these fleet's
harnesses ship async scheduling primitives (Claude Code: `Monitor`,
`ScheduleWakeup`; Antigravity: `schedule` and background task management)
that make a manual OS-level version unnecessary overhead on those
harnesses specifically, even though it may be the correct — or only —
option on a harness with no such primitive.

A pattern that hand-rolls something should say so explicitly: name it as
the fallback it is, and note in `triggers` or the prose that a reader
should check for a native equivalent on their own harness first.
`harnesses_verified` on that entry then records where it was actually
needed, not just where it happens to work.

## The gate

An entry does not merge without `verified_by` set to someone other than
`author` — they have to have actually run `recipe.py` (or followed
`SKILL.md` if there's no recipe) and confirmed it works. Same bar as code
review. Without this the directory rots into unvetted gists nobody trusts.

## Code, not prose

If a pattern is runnable, it ships a `recipe.py`: PEP 723 inline metadata
(`# /// script` block), invoked as `uv run recipe.py`. No virtualenv setup,
no dependency drift — copy the one file, run it.

If a pattern has no runnable form (a design note, a scar with no fix to
automate), the SKILL.md's own fenced code blocks are checked by
`validate.py` (`python3 -m py_compile` on anything tagged `python`) so they
can't bit-rot into pseudo-code silently.

## CI

On PR: `validate.py` checks frontmatter schema and fenced-block syntax.
On merge to `main`: CI regenerates `manifest.json`, `llms.txt`, and the
`README.md` table from the current `<name>/SKILL.md` frontmatter — none of
the three are hand-edited.
