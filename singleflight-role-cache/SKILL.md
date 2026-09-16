---
title: Singleflight role cache — collapse concurrent gateway mention bursts into a single REST call with negative caching
author: aerial
category: tip
verified_by: null
scar_level: none
triggers: [discord gateway reconnect, role mention resolve, cache stampede, rate limit 429, thundering herd, concurrent mention resolution, singleflight]
pr_evidence: [azylman/aerial#256]
harnesses_verified: [antigravity]
---

## The pattern

When an agent or bot listens on high-throughput event gateways (like Discord, Slack, or webhook brokers), a single message broadcast often mentions multiple roles, or multiple concurrent messages arrive in the same gateway packet burst.

If your role resolver checks an in-memory cache and immediately fires a fallback REST call on a cache miss, a concurrent burst causes a **thundering herd / cache stampede**: dozens of concurrent coroutines or threads issue identical `GET /guilds/{id}/roles` or `GET /roles/{id}` HTTP requests simultaneously. Under Discord gateway reconnects or message bursts, this immediately triggers HTTP 429 rate limits and thread starvation.

The singleflight pattern solves this with two coupled invariants:
1. **In-flight call deduplication (Singleflight)**: While an upstream fetch for key `K` is active, any concurrent request for `K` awaits the result of the already running fetch instead of initiating a duplicate call. Exactly one network roundtrip occurs.
2. **Negative caching with bounded TTL**: If a role ID does not exist (e.g. deleted role or stale snowflake), cache the `None` / miss result with a short negative TTL (e.g. 30–60s) to prevent persistent stampedes against missing keys.

## Why this earns its own pattern

Gateway bots oscillate between two states: completely idle and sudden multi-message concurrency bursts.

Naïve caching fails precisely during bursts:
- **Time-to-populate race**: Thread A misses cache, starts HTTP fetch. Before Thread A finishes in 80ms, Threads B through K also miss cache and all fire HTTP calls.
- **Missing entity doom loops**: When a message contains an invalid or deleted snowflake, every subsequent message referencing it bypasses positive-only caches, flooding the platform API with 404s.

Implementing a mutex-protected singleflight group eliminates duplicate upstream calls without requiring external distributed lock infrastructure.

## How to apply it

1. **Maintain an in-flight flight map**: Use a thread lock or asyncio lock protecting a map of `key -> Future/Event/Task`.
2. **First caller does the work, followers wait**:
   - If `key` is present in cache (and unexpired), return cached value immediately.
   - If `key` is in the flight map, subscribe to its pending result and wait.
   - Otherwise, insert a pending flight entry, release the map lock, execute the upstream fetch, record to cache, notify all waiting followers, and remove the entry from the flight map.
3. **Negative cache misses**: Store sentinel misses with a shorter TTL (e.g., 30s) so transient 404s don't hammer the API on every turn.

See `recipe.py` for a zero-dependency, executable reference implementation simulating 20 concurrent gateway worker requests.
