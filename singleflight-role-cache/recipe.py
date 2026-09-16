# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Demonstrate singleflight deduplication and negative caching for gateway bots.

Run:
    uv run singleflight-role-cache/recipe.py
    python3 singleflight-role-cache/recipe.py

Simulates 20 concurrent gateway worker threads querying an uncached role ID
simultaneously, showing that naïve cache triggers 20 network roundtrips (stampede),
while singleflight collapses them into exactly 1 network roundtrip.
Also validates negative caching for non-existent keys.
"""

import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple


class UpstreamMockAPI:
    def __init__(self) -> None:
        self.call_count = 0
        self._lock = threading.Lock()
        self.roles = {
            "role_1486135733214380105": {"id": "role_1486135733214380105", "name": "Agent Operator"},
            "role_1543462881624858624": {"id": "role_1543462881624858624", "name": "Banana Pilot"},
        }

    def fetch_role(self, role_id: str) -> Optional[Dict[str, str]]:
        with self._lock:
            self.call_count += 1
        # Simulate network latency
        time.sleep(0.05)
        return self.roles.get(role_id)


class NaiveCache:
    def __init__(self, fetch_fn: Callable[[str], Any]) -> None:
        self.fetch_fn = fetch_fn
        self.cache: Dict[str, Any] = {}
        self.lock = threading.Lock()

    def get_role(self, role_id: str) -> Any:
        with self.lock:
            if role_id in self.cache:
                return self.cache[role_id]
        # Stampede vulnerability: lock released before network call finishes
        val = self.fetch_fn(role_id)
        with self.lock:
            self.cache[role_id] = val
        return val


class SingleflightCache:
    def __init__(self, fetch_fn: Callable[[str], Any], ttl_seconds: float = 300.0, neg_ttl_seconds: float = 30.0) -> None:
        self.fetch_fn = fetch_fn
        self.ttl = ttl_seconds
        self.neg_ttl = neg_ttl_seconds
        self._cache: Dict[str, Tuple[Any, float]] = {}  # key -> (value, expires_at)
        self._flights: Dict[str, threading.Event] = {}
        self._flight_results: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def get_role(self, role_id: str) -> Any:
        now = time.time()
        with self._lock:
            # 1. Check valid cache entry (positive or negative)
            if role_id in self._cache:
                val, exp = self._cache[role_id]
                if now < exp:
                    return val

            # 2. Check if a flight is already in progress
            if role_id in self._flights:
                event = self._flights[role_id]
                # Wait for leader to finish
                self._lock.release()
                try:
                    event.wait()
                    with self._lock:
                        return self._flight_results.get(role_id)
                finally:
                    self._lock.acquire()

            # 3. We are the leader for this flight
            event = threading.Event()
            self._flights[role_id] = event

        # Leader executes network fetch outside the lock
        try:
            val = self.fetch_fn(role_id)
        finally:
            with self._lock:
                # Save result for waiting followers
                self._flight_results[role_id] = val
                duration = self.ttl if val is not None else self.neg_ttl
                self._cache[role_id] = (val, time.time() + duration)
                del self._flights[role_id]
                event.set()

        return val


def run_benchmark() -> None:
    role_id = "role_1543462881624858624"
    num_threads = 20

    print("--- 1. Testing Naive Cache (Thundering Herd) ---")
    api_naive = UpstreamMockAPI()
    naive_cache = NaiveCache(api_naive.fetch_role)

    threads = []
    results = [None] * num_threads

    def worker_naive(idx: int) -> None:
        results[idx] = naive_cache.get_role(role_id)

    for i in range(num_threads):
        t = threading.Thread(target=worker_naive, args=(i,))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"Naive calls: {api_naive.call_count} upstream calls for {num_threads} concurrent queries")
    assert api_naive.call_count > 1, f"Expected cache stampede, but got {api_naive.call_count} calls"

    print("\n--- 2. Testing Singleflight Cache ---")
    api_singleflight = UpstreamMockAPI()
    sf_cache = SingleflightCache(api_singleflight.fetch_role)

    threads = []
    sf_results = [None] * num_threads

    def worker_sf(idx: int) -> None:
        sf_results[idx] = sf_cache.get_role(role_id)

    for i in range(num_threads):
        t = threading.Thread(target=worker_sf, args=(i,))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"Singleflight calls: {api_singleflight.call_count} upstream call(s) for {num_threads} concurrent queries")
    assert api_singleflight.call_count == 1, f"Expected exactly 1 call, got {api_singleflight.call_count}"
    assert all(r is not None and r["name"] == "Banana Pilot" for r in sf_results)
    print("SUCCESS: 20 concurrent calls collapsed into exactly 1 network roundtrip.")

    print("\n--- 3. Testing Negative Caching (Missing Entity) ---")
    missing_id = "role_non_existent"
    # Query missing role across 5 concurrent threads
    threads = []
    for _ in range(5):
        t = threading.Thread(target=sf_cache.get_role, args=(missing_id,))
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    calls_after_first_burst = api_singleflight.call_count
    # All 5 collapsed into 1 additional call
    assert calls_after_first_burst == 2, f"Expected 2 total calls, got {calls_after_first_burst}"

    # Subsequent query within negative TTL makes zero upstream calls
    val = sf_cache.get_role(missing_id)
    assert val is None
    assert api_singleflight.call_count == 2
    print("SUCCESS: Negative cache prevented redundant upstream fetches for missing role.")


if __name__ == "__main__":
    run_benchmark()
