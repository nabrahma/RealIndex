# SPDX-License-Identifier: GPL-3.0-or-later
"""Job queue runner: background Blender worker processes.

Each job is one process: blender --background --factory-startup --disable-autoexec --python worker.py --
<job.json>. The worker writes <job>.result.json; the pool never imports anything into the UI process.
A job that crashes, exits without a result or passes its timeout is killed and reported as a crash; the
engine retries it once and then lists it under Problems. Pure Python (subprocess), runs on the service
thread; Windows workers get no console window and below-normal priority so Blender's UI stays smooth.
"""

import datetime
import json
import os
import subprocess
import sys
import time

WORKER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "worker", "worker.py")


class Running:
    __slots__ = ("engine", "job", "proc", "started", "timeout", "log_path", "log_fh")

    def __init__(self, engine, job, proc, timeout, log_path, log_fh):
        self.engine, self.job, self.proc = engine, job, proc
        self.started, self.timeout = time.time(), timeout
        self.log_path, self.log_fh = log_path, log_fh


def worker_argv(blender, job_path):
    return [blender, "--background", "--factory-startup", "--disable-autoexec", "--python", WORKER, "--", job_path]


def _popen(argv, log_fh, cwd):
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000 | 0x00004000  # CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
    env = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONHOME", "BLENDER_USER_SCRIPTS"):  # keep the worker a clean factory Blender
        env.pop(k, None)
    return subprocess.Popen(argv, stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=cwd,
                            env=env, **kw)


class Pool:
    """Runs at most max_workers jobs at a time, pulling them from engines round-robin."""

    def __init__(self, blender, max_workers=2):
        self.blender = blender
        self.max_workers = max(1, int(max_workers))
        self.running = []
        self._rr = 0

    def busy_for(self, engine):
        return [r for r in self.running if r.engine is engine]

    def step(self, engines):
        """Reap finished and timed-out jobs, then start new ones. Returns the number of jobs finished."""
        done = 0
        now = time.time()
        for r in list(self.running):
            code = r.proc.poll()
            timed_out = code is None and now - r.started > r.timeout
            if code is None and not timed_out:
                continue
            if timed_out:
                r.proc.kill()
                try:
                    r.proc.wait(5)
                except subprocess.TimeoutExpired:
                    pass
            self.running.remove(r)
            r.log_fh.close()
            log = _read_tail(r.log_path)
            _append_log(r.engine.state, r.job, log)
            try:
                os.remove(r.log_path)
            except OSError:
                pass
            result = None
            rp = r.job.get("result")
            if rp and os.path.exists(rp):
                try:
                    with open(rp, encoding="utf-8") as fh:
                        result = json.load(fh)
                except (OSError, ValueError):
                    result = None
            if result is None:
                reason = ("Timed out after %d s" % r.timeout) if timed_out else (
                    "Worker crashed (exit code %s)" % code)
                result = {"ok": False, "crash": True, "error": reason, "log": log[-2000:]}
            result["seconds"] = round(time.time() - r.started, 2)
            r.engine.on_job_done(r.job, result)
            done += 1
        live = [e for e in engines if e.can_run_jobs()]
        idle_rounds = 0
        while len(self.running) < self.max_workers and live and idle_rounds < len(live):
            e = live[self._rr % len(live)]
            self._rr += 1
            job = e.next_job()
            if job is None:
                idle_rounds += 1
                continue
            idle_rounds = 0
            self.start(e, job)
        return done

    def start(self, engine, job):
        jobs_dir = os.path.join(engine.state, "jobs")
        os.makedirs(jobs_dir, exist_ok=True)
        job_path = os.path.join(jobs_dir, "job_%s.json" % job["job"])
        if not job.get("result"):
            job["result"] = os.path.join(jobs_dir, "job_%s.result.json" % job["job"])
        try:
            os.remove(job["result"])
        except OSError:
            pass
        with open(job_path, "w", encoding="utf-8") as fh:
            json.dump(job, fh, indent=1)
        log_path = os.path.join(jobs_dir, "job_%s.log" % job["job"])
        log_fh = open(log_path, "wb")
        argv = job.get("argv") or worker_argv(self.blender, job_path)
        try:
            proc = _popen(argv, log_fh, jobs_dir)
        except OSError as e:
            log_fh.close()
            engine.on_job_done(job, {"ok": False, "crash": True, "error": "Could not start Blender: %s" % e})
            return
        self.running.append(Running(engine, job, proc, float(job.get("timeout", 120)), log_path, log_fh))

    def stop_all(self):
        for r in self.running:
            try:
                r.proc.kill()
            except OSError:
                pass
            r.log_fh.close()
        self.running = []

    def cancel(self, engine, asset_id):
        """Kill a running job of an asset (used when its source vanished mid-conversion)."""
        for r in list(self.running):
            if r.engine is engine and r.job.get("asset_id") == asset_id:
                r.proc.kill()


def _read_tail(path, limit=20000):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            n = fh.tell()
            fh.seek(max(0, n - limit))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _append_log(state, job, text):
    logs = os.path.join(state, "logs")
    os.makedirs(logs, exist_ok=True)
    path = os.path.join(logs, "worker_%s.log" % datetime.date.today().isoformat())
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("==== job %s %s %s %s\n" % (job.get("job"), job.get("kind"), job.get("asset_type", ""),
                                             job.get("name", "")))
        fh.write(text)
        fh.write("\n")
