"""What a run costs the machine: RAM and CPU of every process in it, Webots' own GPU memory and the card's.

A Sampler watches groups of processes: a session (Webots under xvfb-run with its controllers: every process whose
session id is the leader's) and Docker containers (their cgroup's processes, and its CPU counter, which keeps the
time of processes that exited). Every second it adds up each group's resident memory and reads nvidia-smi: the
card's memory in use, and each process's framebuffer (pmon), so Webots' share is told apart from the desktop's.
result() gives the peaks and CPU seconds; a measurement that could not be taken is listed under "missing", never
recorded as 0. Standard library only: the launcher runs on the system Python.
"""
import os
from pathlib import Path
import subprocess
import threading
import time

PAGE = os.sysconf("SC_PAGE_SIZE")
TICK = os.sysconf("SC_CLK_TCK")


def session_processes(session):
    """{pid: (resident bytes, CPU seconds)} for every process in a session."""
    found = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[3]) != session:
                continue
            resident = int((entry / "statm").read_text().split()[1]) * PAGE
        except (OSError, IndexError, ValueError):
            continue  # exited meanwhile
        found[int(entry.name)] = (resident, (int(fields[11]) + int(fields[12])) / TICK)
    return found


def nvidia_smi(*args):
    """nvidia-smi's output, or None when it is missing or fails."""
    try:
        done = subprocess.run(["nvidia-smi", *args], capture_output=True, text=True)
    except OSError:
        return None
    return None if done.returncode else done.stdout


def framebuffers(pmon):
    """{pid: MiB} from `nvidia-smi pmon -s m`: one row per process (gpu, pid, type, fb, ccpm, command)."""
    found = {}
    for row in pmon.splitlines():
        columns = row.split()
        if len(columns) > 3 and columns[1].isdigit() and columns[3].isdigit():
            found[int(columns[1])] = int(columns[3])
    return found


class Sampler(threading.Thread):
    def __init__(self, sessions=(), containers=(), interval=1.0):
        """sessions: {name: session id}; containers: {name: Docker container name}."""
        super().__init__(daemon=True)
        self.sessions, self.containers, self.interval = dict(sessions), dict(containers), interval
        self.cgroups = {}
        self.peak = {name: 0 for name in (*self.sessions, *self.containers)}
        self.peak_total = self.samples = 0
        self.cpu = {}  # session: {pid: CPU seconds}; container: its cgroup's counter
        self.gpu = {"webots_peak_mib": None, "card_peak_mib": None}
        self.missing = set()
        self.history = []  # each sample: seconds since the start, total resident MiB, the card's MiB
        self.started = time.monotonic()
        self.done = threading.Event()

    def run(self):
        self.sample()
        while not self.done.wait(self.interval):
            self.sample()

    def stop(self):
        self.done.set()
        self.join()
        self.sample()

    def cgroup(self, name, container):
        if name not in self.cgroups:
            found = subprocess.run(["docker", "inspect", "-f", "{{.Id}}", container], capture_output=True, text=True)
            if found.returncode:
                return None  # not started yet
            self.cgroups[name] = Path(f"/sys/fs/cgroup/system.slice/docker-{found.stdout.strip()}.scope")
        return self.cgroups[name]

    def sample(self):
        total, pids = 0, {}
        for name, session in self.sessions.items():
            processes = session_processes(session)
            pids[name] = set(processes)
            resident = sum(r for r, _ in processes.values())
            cpu = self.cpu.setdefault(name, {})
            for pid, (_, seconds) in processes.items():
                cpu[pid] = max(cpu.get(pid, 0.0), seconds)
            self.peak[name], total = max(self.peak[name], resident), total + resident
        for name, container in self.containers.items():
            cgroup = self.cgroup(name, container)
            try:
                procs = [int(p) for p in (cgroup / "cgroup.procs").read_text().split()] if cgroup else []
                usage = next(int(line.split()[1]) for line in (cgroup / "cpu.stat").read_text().splitlines()
                             if line.startswith("usage_usec")) if cgroup else None
            except OSError:
                continue  # the container stopped
            resident = 0
            for pid in procs:
                try:
                    resident += int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * PAGE
                except OSError:
                    pass
            if usage is not None:
                self.cpu[name] = usage / 1e6
            self.peak[name], total = max(self.peak[name], resident), total + resident
        self.peak_total = max(self.peak_total, total)
        self.samples += 1
        card = nvidia_smi("--query-gpu=memory.used", "--format=csv,noheader,nounits")
        used = int(card.split()[0]) if card and card.split()[0].isdigit() else None
        if used is None:
            self.missing.add("card memory (nvidia-smi --query-gpu)")
        else:
            self.gpu["card_peak_mib"] = max(self.gpu["card_peak_mib"] or 0, used)
        self.history.append({"t": round(time.monotonic() - self.started, 1), "rss_mib": round(total / 2**20),
                             "card_mib": used})
        webots = pids.get("webots")
        if webots is None:
            return
        pmon = nvidia_smi("pmon", "-c", "1", "-s", "m")
        if pmon is None:
            self.missing.add("Webots' GPU memory (nvidia-smi pmon)")
            return
        framebuffer = sum(mib for pid, mib in framebuffers(pmon).items() if pid in webots)
        self.gpu["webots_peak_mib"] = max(self.gpu["webots_peak_mib"] or 0, framebuffer)

    def result(self):
        missing = set(self.missing)
        missing |= {f"{name} (never sampled)" for name in self.containers if name not in self.cpu}
        if "webots" in self.sessions and not self.gpu["webots_peak_mib"]:
            missing.add("Webots' GPU memory (no framebuffer seen)")
        return {
            "samples": self.samples,
            "peak_rss_mib": {**{name: round(v / 2**20) for name, v in self.peak.items()},
                             "total": round(self.peak_total / 2**20)},
            "cpu_s": {name: round(sum(v.values()) if isinstance(v, dict) else v, 1) for name, v in self.cpu.items()},
            "gpu_mib": self.gpu,
            "missing": sorted(missing),
        }
