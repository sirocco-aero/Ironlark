"""Run an offline forest-build stage without exhausting the workstation."""

import json
import os
from pathlib import Path
import signal
import subprocess
import time


def memory_fields(path):
    fields = {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition(":")
        if value.strip().endswith("kB"):
            fields[key] = int(value.split()[0])
    return fields


def process_memory(pid):
    """Count the complete stage, including make's compiler children."""
    rss = swapped = 0
    pending = [pid]
    while pending:
        child = pending.pop()
        try:
            fields = memory_fields(Path(f"/proc/{child}/status"))
            rss += fields.get("VmRSS", 0)
            swapped += fields.get("VmSwap", 0)
            children = Path(f"/proc/{child}/task/{child}/children").read_text()
            pending.extend(map(int, children.split()))
        except (FileNotFoundError, ProcessLookupError):
            continue
    return rss, rss + swapped


def run_stage(command, log_path, max_mib=4500):
    """Bound RSS plus swapped process pages, retaining logs and measurements."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    peak_rss = peak_committed = 0
    error = None
    with log_path.open("w") as log:
        proc = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            while proc.poll() is None:
                rss, committed = process_memory(proc.pid)
                peak_rss = max(peak_rss, rss)
                peak_committed = max(peak_committed, committed)
                available = memory_fields(Path("/proc/meminfo"))["MemAvailable"]
                if committed > max_mib * 1024 or available < 768 * 1024:
                    error = f"Stopped before memory exhaustion ({committed / 1024:.0f} MiB process memory); see {log_path}"
                    break
                time.sleep(0.25)
            if not error and proc.wait() != 0:
                error = f"Build stage exited {proc.returncode}; see {log_path}"
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            log_path.with_suffix(".metrics.json").write_text(
                json.dumps(
                    {
                        "peak_rss_mib": round(peak_rss / 1024, 1),
                        "peak_rss_and_swap_mib": round(peak_committed / 1024, 1),
                        "seconds": round(time.monotonic() - started, 1),
                        "exit_code": proc.returncode,
                        "error": error,
                    },
                    indent=2,
                )
            )
    if error:
        raise RuntimeError(error)
