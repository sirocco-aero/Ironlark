"""Run an offline forest-build stage without exhausting the workstation."""

from collections import deque
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time


def error_excerpt(log_path, limit=18):
    """Prefer compiler/linker diagnostics over interleaved make progress."""
    pattern = re.compile(r"fatal error:|\berror:|undefined reference|multiple definition|"
                         r"No rule to make target|cannot find|ld:|linker command failed|"
                         r"Permission denied|Operation not permitted|No space left on device|"
                         r"Disk quota exceeded|Killed signal|out of memory", re.I)
    if limit <= 0:
        return ""
    selected = {}
    tail = deque(maxlen=limit)
    previous = None
    context_until = -1
    try:
        with Path(log_path).open(errors="replace") as stream:
            for index, raw in enumerate(stream):
                line = raw.rstrip("\r\n")[:500]
                tail.append(line)
                if pattern.search(line):
                    if previous is not None and len(selected) < limit:
                        selected.setdefault(index - 1, previous)
                    context_until = index + 2
                if index <= context_until and len(selected) < limit:
                    selected.setdefault(index, line)
                previous = line
    except OSError:
        return ""
    return "\n".join(selected.values() if selected else tail)


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
    proc = None
    with log_path.open("w") as log:
        try:
            proc = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
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
        except KeyboardInterrupt:
            error = f"Build stage interrupted; see {log_path}"
            raise
        except OSError as exc:
            error = f"Build stage failed: {exc}; see {log_path}"
            log.write(error + "\n")
        finally:
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                except ProcessLookupError:
                    proc.wait()
            log_path.with_suffix(".metrics.json").write_text(
                json.dumps(
                    {
                        "peak_rss_mib": round(peak_rss / 1024, 1),
                        "peak_rss_and_swap_mib": round(peak_committed / 1024, 1),
                        "seconds": round(time.monotonic() - started, 1),
                        "exit_code": proc.returncode if proc is not None else None,
                        "error": error,
                    },
                    indent=2,
                )
            )
    if error:
        excerpt = error_excerpt(log_path)
        raise RuntimeError(error + ("\n" + excerpt if excerpt else ""))
