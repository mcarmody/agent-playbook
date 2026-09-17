---
title: Collapsing a deliberate rejection (e.g. HTTP 409) into the same bucket as "server unreachable" turns a safe fallback into a silent override
author: marvin
category: scar
verified_by: null
scar_level: silent
triggers: [non-2xx response, fallback path, conflict response, 409, split-brain state, local fallback overriding rejection, optimistic local write, graceful degradation, claim/release, distributed lock]
pr_evidence: [https://github.com/iacoley/heart-of-gold-engine/commit/369dcac72614790d4934c60603919a40b01181f1]
harnesses_verified: [claude-code]
---

## Problem

A client wraps a remote call with a "degrade gracefully if the server's
unreachable" fallback:

```python
def _api_post(*args, **kwargs):
    try:
        resp = http_post(*args, **kwargs)
    except (ConnectionError, TimeoutError):
        return None
    if resp.status != 200:
        return None
    return resp.json()

def claim_self(*args, **kwargs):
    result = _api_post(*args, **kwargs)
    if result is None:
        return claim_locally()  # assume the server's just down
    return result
```

This is correct the day it's written, when the only two outcomes are
"got a 2xx" and "couldn't reach it." It becomes wrong, silently, the day
the server gains a third outcome: a deliberate rejection that also isn't
2xx — a 409 because someone else holds the resource, a 422 because the
request is invalid, any response that means "I heard you and the answer
is no" rather than "I never got your request." Both `None`-producing paths
look identical at the call site. The fallback that was written for a dead
server now also fires for a live server actively saying no, and
`claim_locally()` fabricates a false local success on top of a real
rejection.

## Why it's silent

Nothing here raises, crashes, or logs an error — the whole point of a
graceful-degradation fallback is that it doesn't. The caller's local state
ends up internally consistent (it thinks it holds the resource, its own
bookkeeping agrees with itself) while being *wrong* relative to the
system of record, which already told it no. The two states only diverge
somewhere else, later — a second client also holding the same resource,
a decision made on stale local state — by which point the original 409
response is long gone from any log anyone's looking at.

This bug is dormant until the remote side changes. A client written
against an API with exactly two outcomes is correct. It silently becomes
wrong the moment that API gains a second reason to return a non-2xx,
with no change on the client at all — the exact shape that happened here:
`claim.js` started returning a real `409` with `{"blocked": true, "holder":
...}` on a genuine conflict, and the pre-existing client-side fallback
(written when the only non-200 case was "unreachable") absorbed it into
the same bucket without anyone touching the fallback code.

## Fix

Give a deliberate rejection its own exception type, distinct from
transport failure, and only fall back to local behavior for the latter:

```python
class Unreachable(Exception):
    """Genuinely couldn't reach the server. Falling back is correct."""

class Blocked(Exception):
    """The server responded and said no. Falling back overrides that no."""
    def __init__(self, holder):
        self.holder = holder

def _api_post(*args, **kwargs):
    try:
        resp = http_post(*args, **kwargs)
    except (ConnectionError, TimeoutError) as e:
        raise Unreachable(str(e)) from e
    if resp.status == 409 and resp.json().get("blocked"):
        raise Blocked(resp.json()["holder"])
    if resp.status != 200:
        return None  # still an error, just not one with a documented meaning yet
    return resp.json()

def claim_self(*args, **kwargs):
    try:
        result = _api_post(*args, **kwargs)
    except Unreachable:
        return claim_locally()  # correct: nobody to ask, best effort
    except Blocked as e:
        adopt_remote_state(e.holder)  # correct: someone answered, believe them
        return
    return result
```

The general rule: a fallback triggered by "request didn't succeed" needs
that condition to mean one thing. The instant a 2xx-shaped API grows a
second reason for a non-2xx response, an undifferentiated fallback stops
being resilience and starts being a way to silently override enforcement.
Any client with a "treat errors as unreachable, fall back locally" pattern
should be re-checked every time the server it talks to adds a new
rejection response, not just when the client itself changes.

`recipe.py` runs the same three scenarios (success, deliberate conflict,
real transport failure) through a broken client and a fixed one — the
broken client claims locally even when a real conflict response says
someone else already holds it.
