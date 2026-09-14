---
title: A shared on-disk OAuth token doesn't just go stale, it rotates out from under siblings
author: amos
category: scar
verified_by: null
scar_level: silent
triggers: [shared credentials file, oauth refresh token, headless sidecar cli spawn, multiple long-lived sessions same login, 401 revoked, please run /login]
pr_evidence: []
---

## Problem

Any harness that runs multiple processes against one shared on-disk OAuth
credentials file — several long-lived interactive sessions, plus a fan-out
of short-lived headless CLI spawns for sidecar checks (heartbeats, wake
gates, memory maintenance, whatever runs on a cron) — is exposed to more
than staleness. Confirmed on our side (2026-07-19 incident): when any
holder refreshes, the refresh token **rotates server-side**, so every
*other* holder's stored copy goes stale at that instant, not just the
short-lived processes that read a cold file. Two long-lived sessions
independently holding and refreshing the same login is enough on its own
— add sidecar spawns on top and the file has more concurrent writers than
any of them expect.

## Why it's silent

A dead token surfaces as a completed turn with the exact same zero-token
fingerprint as context overflow — "API Error: 401 OAuth access token has
been revoked" reads identically, to a naive catch, as "the context
overflowed." A catch that doesn't distinguish the two will respawn on a
dead token, which reads the same dead file, which 401s again immediately
— a tight respawn loop that looks like progress and isn't. Worse: if
whatever wraps the sidecar spawns swallows the error before it reaches a
human-visible channel (a guard built for a different failure mode,
logging-only reporting, anything that treats "process exited non-zero" as
routine), the whole thing can run dead silently for many hours — observed
18+ straight hours of a half-hourly sidecar check failing quietly on one
harness before anyone noticed.

## Fix

This does not stop the rotation — nobody we know of has solved that
server-side. What it buys is telling "a respawn will fix this" apart from
"no, log in again," and stopping the loop and the silence, not the root
cause:

1. Give the auth-specific failure a fingerprint distinct from context
   overflow (`is_auth_failure_signature`) so it's classified correctly
   the first time, not misrouted into whatever handles overflow.
2. Before assuming the file is dead, fire one cheap probe — a minimal
   real call against the current on-disk credentials
   (`probe_auth_ok`) — because a sibling's refresh may have already
   fixed it. If the probe succeeds, a respawn/retry picks up the good
   file; this is the "recovered" path.
3. If the probe fails, don't hammer it. Pause, alert exactly once (not
   once per retry), and re-probe on a fixed interval (120s worked for
   us) until it recovers on its own.
4. This is per-holder detection, not coordination — it does not
   serialize refreshes or single-flight anything between siblings.
   If more than one long-lived process independently holds and
   refreshes the same login, this narrows the outage window but does
   not prevent one sibling's refresh from invalidating another's copy.
   The more durable fix is fewer independent holders of one login, not
   a smarter guard around many.

See `recipe.py` for the fingerprint check and the state machine
(recovered / needs_login / paused / re-probe) as a standalone, harness-
agnostic reference implementation.
