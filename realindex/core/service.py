# SPDX-License-Identifier: GPL-3.0-or-later
"""The background service: one daemon thread that polls every root's engine, runs the worker pool and
executes commands from the UI. Blender's UI thread only queues commands and reads snapshots and events,
so a RealIndex timer tick costs microseconds. Pure Python (no bpy)."""

import os
import queue
import threading
import time
import traceback

from . import engine as E
from . import fsutil, jobs


class Service:
    def __init__(self, blender, settings=None):
        self.blender = blender
        self.settings = settings or E.Settings()
        self.pool = jobs.Pool(blender, self.settings.workers)
        self.engines = []
        self.cmds = queue.Queue()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self._events = []
        self._snap = {}
        self.errors = []
        self.thread = None
        self._stop = False
        self._last_poll = {}
        self._last_disk = 0.0

    # ----------------------------------------------------------------------------------- lifecycle
    def start(self, roots):
        self.command(None, "_configure", roots, self.settings)
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, name="RealIndex", daemon=True)
            self.thread.start()

    def stop(self, timeout=5.0):
        self._stop = True
        self.wake.set()
        if self.thread is not None:
            self.thread.join(timeout)
        self.pool.stop_all()
        for e in self.engines:
            try:
                e.close()
            except Exception:
                pass
        self.engines = []
        self.thread = None

    def reconfigure(self, roots, settings):
        self.command(None, "_configure", roots, settings)

    # ------------------------------------------------------------------------------------ commands
    def command(self, root, name, *args, **kw):
        """Queue engine.<name>(*args) for the root (a path) or, with root None, a service method."""
        self.cmds.put((root, name, args, kw))
        self.wake.set()

    def _configure(self, roots, settings):
        self.settings = settings
        self.pool.max_workers = max(1, int(settings.workers))
        want = {fsutil.norm(r.path): r for r in roots if r.enabled}
        keep = []
        for e in self.engines:
            r = want.pop(fsutil.norm(e.root), None)
            if r is None or fsutil.norm(r.state) != fsutil.norm(e.state):
                for run in self.pool.busy_for(e):
                    run.proc.kill()
                e.close()
                if r is not None:
                    want[fsutil.norm(r.path)] = r
                continue
            e.settings = settings
            e.cfg = r
            keep.append(e)
        for r in want.values():
            try:
                keep.append(E.Engine(r, settings))
            except Exception as ex:
                self.errors.append("Could not open %s: %s" % (r.path, ex))
        self.engines = keep

    def _run_cmd(self, root, name, args, kw):
        if root is None:
            return getattr(self, name)(*args, **kw)
        for e in self.engines:
            if fsutil.norm(e.root) == fsutil.norm(root):
                if name == "publish":
                    return e.publish(self.blender, *args, **kw)
                return getattr(e, name)(*args, **kw)

    def pause(self, value=True):
        for e in self.engines:
            e.paused = value

    def sync_all(self):
        for e in self.engines:
            e.sync_now()

    # ---------------------------------------------------------------------------------------- loop
    def _loop(self):
        while not self._stop:
            try:
                self.step()
            except Exception:
                self.errors.append(traceback.format_exc())
                self.errors = self.errors[-20:]
            self.wake.wait(0.25)
            self.wake.clear()

    def step(self, force_poll=False):
        """One service iteration (tests call this directly instead of starting the thread)."""
        while True:
            try:
                root, name, args, kw = self.cmds.get_nowait()
            except queue.Empty:
                break
            try:
                self._run_cmd(root, name, args, kw)
            except Exception:
                self.errors.append(traceback.format_exc())
        now = time.time()
        for e in self.engines:
            last = self._last_poll.get(e.root, 0.0)
            if force_poll or now - last >= float(self.settings.poll_interval):
                self._last_poll[e.root] = now
                try:
                    e.tick()
                except Exception:
                    self.errors.append(traceback.format_exc())
        self.pool.step(self.engines)
        if now - self._last_disk > 60.0:
            self._last_disk = now
            for e in self.engines:
                try:
                    e.disk_usage()
                except Exception:
                    pass
        snap = {}
        events = []
        for e in self.engines:
            snap[e.root] = e.snapshot(self.pool.busy_for(e))
            events += e.events
            e.events = []
        with self.lock:
            self._snap = snap
            self._events += events

    # -------------------------------------------------------------------------------- UI read side
    def snapshot(self):
        with self.lock:
            return dict(self._snap)

    def drain_events(self):
        with self.lock:
            ev, self._events = self._events, []
        return ev

    def states(self):
        return [(e.root, e.state) for e in self.engines]


def worker_exists():
    return os.path.exists(jobs.WORKER)
