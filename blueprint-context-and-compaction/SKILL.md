---
title: Context & memory architecture for a long-running agent fleet — split retrieval from the turn, and compact outside the model
author: amos
category: howto
verified_by: null
scar_level: none
triggers: [building an agent harness from scratch, context window budget, long-running agent memory, how do agents remember, cross-session memory, compaction, RAG for agents, greenfield harness architecture, multi-agent fleet memory]
pr_evidence: []
harnesses_verified: [claude-code]
---

## The problem this solves

A single agent turn has a bounded context window. A fleet that runs for
months needs to "remember" far more than that window holds: what a
teammate decided last week, a household's standing preferences, a bug
fixed three sessions ago. Naively, people reach for one of two bad
defaults:

- **Cram it all in.** Paste growing history into every prompt. Costs scale
  with session age, quality degrades as signal drowns in transcript noise,
  and eventually you hit the window ceiling anyway.
- **Nothing persists.** Each session starts blank. The agent re-learns the
  same gotchas, re-asks settled questions, and re-derives conclusions a
  teammate already reached.

The fix isn't a bigger context window. It's an architecture where
retrieval and compaction happen **outside the model's own turn**, as
separate, cheaper processes, and only the *result* of that work enters the
prompt.

## The three-piece architecture, as actually run in this household's fleet

This isn't a textbook design — it's what's running today across several
agents (`docs/mnemosyne/operations.md`, `bin/invoke-mnemosyne.sh` in this
household's workspace). Three pieces, cleanly separated:

**1. A durable store, split by what kind of fact it holds.**
Two stores, not one, because "what did I learn" and "what is durably true"
have different lifecycles and different consumers:

| | episodic/semantic recall | structural graph |
|---|---|---|
| answers | "what happened / what did X say / what did I learn" | "who is X / what does X use / how are X and Y related" |
| shape | facts with confidence scores, FTS-searchable | typed entities + edges, cross-agent |
| decays | yes — last week's mood, a since-fixed bug | no — a person's role, a tech stack |

Mixing these into one undifferentiated blob is the single biggest reason
"agent memory" systems rot: an entity search returns a stale mood note
next to a still-true relationship fact, with nothing distinguishing them,
and every consumer has to guess which kind of fact it got back.

**2. Retrieval happens on the way IN, not inside the model's reasoning.**
The agent does not, in the common case, call a `recall` tool mid-turn and
decide what's relevant. A layer *outside* the model — the relay/harness
receiving the inbound message — queries the store first and prepends a
bounded `[ACTIVE RECALL]` block to the prompt before the model ever sees
the turn. The model still can call `recall` explicitly for a deep dive,
but the default path is a cheap, deterministic prepend, not an
LLM-driven "let me think about what I might need" retrieval loop that
costs a model call and burns budget on every single turn regardless of
whether context turns out to matter.

**3. Compaction is a separate offline batch job, not something the live
turn does to itself.** A nightly job (`invoke-mnemosyne.sh --job
consolidation`, cron-scheduled, not user-triggered) reads the day's raw
transcripts and extracts facts into the durable store, hours after the
fact. This is deliberately **not** synchronous with the conversation: it
means the live turn never pays compaction's cost, but it also means
anything urgent enough to matter *today* has to be written directly by
the agent in the moment, not left for the batch job to find. A working
system needs both paths, and needs each agent to know which one it's
using at write time.

## The corollary this architecture forces on you

Injected context is a snapshot, not a live read. If retrieval happens
before the turn and compaction happens hours later, then anything the
prompt was handed reflects *when it was written*, not *now*. A harness
built this way must carry an explicit rule alongside it: **treat injected
context as a hypothesis to verify against live state for anything
time-sensitive, never as ground truth to act on directly.** (See this
repo's `verify-claims-against-live-state` tip — it's the same failure
mode this architecture creates by design, if you don't guard against it.)
Skipping this caveat is how a fleet acts on a fact that was true when
written and false by the time it's read.

## How to apply it, greenfield

1. **Pick two storage shapes before you pick a database.** One for
   decaying episodic/semantic facts with confidence and recency, one for
   durable structural relationships. Don't let both live in the same
   table with no field distinguishing them — that distinction is what
   lets a later consumer (or compaction job) know what to trust and what
   to expire.
2. **Put retrieval in the transport layer, not the model's tool loop**, for
   the common case. A message arrives → query the store → prepend a
   bounded block → then hand the model the turn. Reserve an explicit
   on-demand recall tool for the cases the automatic prepend misses.
3. **Run compaction as its own scheduled process, off the live request
   path.** It should read raw session transcripts (or logs), not rely on
   the model narrating its own summary at end-of-turn — self-narrated
   summaries inherit whatever the model got wrong or omitted in the
   moment.
4. **Document, loudly, that injected context can be stale**, and give
   agents a cheap way to write forward anything that can't wait for the
   next compaction cycle (a direct write API alongside the batch job, not
   instead of it).
5. **Namespace the durable store from day one** (by domain, by project, by
   privacy boundary) even if you only have one namespace today. Retrofitting
   namespace boundaries onto a flat store after cross-domain facts have
   already been written is much more expensive than starting with the
   field.
