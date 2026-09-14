# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
recipe.py — detached background monitor pattern for asynchronous CI and long-running tasks.

Replaces toxic synchronous `while CI: sleep(10)` loops inside model turns with an
isolated, detached OS daemon that tracks progress, guards timeouts, and writes a
structured outcome artifact.

Run directly: `uv run recipe.py --demo`
No external dependencies beyond the Python standard library.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def launch_detached_monitor(
    job_id: str,
    work_dir: Path,
    poll_command: list[str],
    timeout_seconds: int = 300,
    poll_interval: float = 2.0,
) -> Path:
    """Launch a detached background monitor process that survives the parent agent turn.

    Returns the path to the status artifact file that the agent inspects upon wakeup.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    status_file = work_dir / f"{job_id}.status.json"
    log_file = work_dir / f"{job_id}.monitor.log"
    pid_file = work_dir / f"{job_id}.pid"

    initial_status = {
        "job_id": job_id,
        "state": "running",
        "started_at": time.time(),
        "completed_at": None,
        "exit_code": None,
        "error": None,
    }
    status_file.write_text(json.dumps(initial_status, indent=2))

    daemon_code = f"""
import json, os, subprocess, sys, time
from pathlib import Path

status_file = Path({str(status_file)!r})
log_file = Path({str(log_file)!r})
poll_cmd = {poll_command!r}
timeout_sec = {timeout_seconds}
poll_interval = {poll_interval}

start_time = time.time()
outcome = "unknown"
exit_code = 1
error_msg = None

with open(log_file, "a") as log:
    log.write(f"Monitor started for job at {{start_time}}\\n")
    while time.time() - start_time < timeout_sec:
        try:
            res = subprocess.run(poll_cmd, capture_output=True, text=True)
            log.write(f"Poll check: exit={{res.returncode}}\\n")
            if res.returncode == 0:
                outcome = "success"
                exit_code = 0
                break
            elif res.returncode != 1:
                outcome = "failed"
                exit_code = res.returncode
                error_msg = res.stderr.strip()
                break
        except Exception as e:
            error_msg = str(e)
            outcome = "error"
            break
        time.sleep(poll_interval)
    else:
        outcome = "timeout"
        error_msg = f"Timed out after {{timeout_sec}} seconds"

final_data = {{
    "job_id": {job_id!r},
    "state": outcome,
    "started_at": start_time,
    "completed_at": time.time(),
    "exit_code": exit_code,
    "error": error_msg,
}}
status_file.write_text(json.dumps(final_data, indent=2))
log.write(f"Monitor finalized: state={{outcome}}, exit={{exit_code}}\\n")
"""

    # Spawn fully detached: start_new_session=True decouples process group from parent
    proc = subprocess.Popen(
        [sys.executable, "-c", daemon_code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_file.write_text(str(proc.pid))
    return status_file


def _demo() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="detached-ci-monitor-"))
    print(f"1. Setting up demo workspace in {tmp}")

    # Create a mock probe script that simulates a CI job pending for 1.5s then succeeding
    ci_probe = tmp / "ci_probe.py"
    ci_probe.write_text("""
import time, sys
from pathlib import Path

flag = Path("ci_clock.tmp")
if not flag.exists():
    flag.write_text(str(time.time()))
    sys.exit(1)

elapsed = time.time() - float(flag.read_text())
if elapsed < 1.5:
    sys.exit(1)
sys.exit(0)
""")

    print("2. Spawning detached background monitor...")
    t0 = time.time()
    status_file = launch_detached_monitor(
        job_id="pr-186-ci",
        work_dir=tmp,
        poll_command=[sys.executable, str(ci_probe)],
        timeout_seconds=10,
        poll_interval=0.4,
    )
    spawn_time = time.time() - t0
    print(f"   Parent spawned daemon in {spawn_time*1000:.1f}ms and is ready to yield immediately!")

    print("3. Simulating model turn yielding floor while daemon monitors independently...")
    for _ in range(15):
        time.sleep(0.3)
        data = json.loads(status_file.read_text())
        if data["state"] != "running":
            print(f"   Monitor completed asynchronously: state='{data['state']}', exit_code={data['exit_code']}")
            break
    else:
        print("Demo error: daemon did not finish in expected window")
        sys.exit(1)

    print("4. Verification complete: daemon executed out-of-band without blocking parent process.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="run the self-contained demonstration")
    args = parser.parse_args()
    if args.demo:
        _demo()
    else:
        parser.print_help()
