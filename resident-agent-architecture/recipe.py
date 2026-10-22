#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Actively probes a local workspace and host environment for the 4 Resident Agent pillars."""

import json
import pathlib
import sys


def _shallow_find(root: pathlib.Path, patterns: tuple[str, ...], limit: int = 3) -> list[str]:
    """Match shallow glob patterns without walking deep .git or runtime trees."""
    hits: list[str] = []
    for pat in patterns:
        for p in root.glob(pat):
            if ".git" in p.parts:
                continue
            rel = str(p.relative_to(root))
            if rel not in hits:
                hits.append(rel)
            if len(hits) >= limit:
                return hits
    return hits


def probe_workspace(root: pathlib.Path) -> dict:
    # 1. Persistent Identity / Daemon supervisor evidence
    daemon_markers = _shallow_find(
        root,
        (
            "Dockerfile",
            "docker-compose.yml",
            "compose.yaml",
            "*.nomad",
            "nomad/*.nomad",
            "nomad/*/*.nomad",
            "jobs/*.nomad",
            "*.service",
            "systemd/*.service",
            "tools/bridge.py",
        ),
        limit=4,
    )

    # 2. Durable Memory store evidence
    memory_markers = _shallow_find(
        root,
        (
            "memory",
            "data/*.db",
            "data/*.sqlite*",
            "MEMORY.md",
            "*/*MEMORY.md",
            "schema.sql",
            "*/*schema.sql",
            "rules/*.md",
            ".agents/rules/*.md",
            "AGENTS.md",
            "CLAUDE.md",
            "GEMINI.md",
        ),
        limit=4,
    )

    # 3. Scheduled Sidecars / Cron evidence
    schedule_markers = _shallow_find(
        root,
        (
            "data/schedule.json",
            "config/schedule*",
            "tools/*scheduler*.py",
            "tools/sidecars.py",
            "mcp/*scheduler*",
            "nomad/*scheduler*.nomad",
            "nomad/*/*scheduler*.nomad",
            "jobs/*scheduler*.nomad",
            "bin/*daily*",
            "bin/*cron*",
        ),
        limit=4,
    )

    # 4. Multi-Agent Peer Coordination / Handoff evidence
    coord_markers = _shallow_find(
        root,
        (
            "tools/banana.py",
            "tools/handoff.py",
            "specs/*handoff*",
            ".agents/rules/*crab_cavern*",
            "peer_contracts.md",
            "*/*peer_contracts.md",
            "services/coven*",
            "channels/*banana*.md",
        ),
        limit=4,
    )

    pillars = {
        "persistent_identity": {"detected": bool(daemon_markers), "evidence": daemon_markers},
        "durable_memory": {"detected": bool(memory_markers), "evidence": memory_markers},
        "scheduled_sidecars": {"detected": bool(schedule_markers), "evidence": schedule_markers},
        "peer_coordination": {"detected": bool(coord_markers), "evidence": coord_markers},
    }
    detected_count = sum(1 for v in pillars.values() if v["detected"])
    return {
        "workspace": str(root.resolve()),
        "pillars_detected": f"{detected_count}/4",
        "classification": "resident_agent" if detected_count >= 3 else "partial_or_ephemeral_workspace",
        "pillars": pillars,
    }


def main() -> int:
    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path.cwd()
    if not target.exists():
        print(json.dumps({"error": f"Path does not exist: {target}"}), file=sys.stderr)
        return 1
    report = probe_workspace(target)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
