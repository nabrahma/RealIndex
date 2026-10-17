"""Worker failure: a worker that crashes or hangs past its timeout fails only that job, which is retried once
and then lands in Problems; the other jobs finish. Real process pool, stand-in worker."""

import os
import sys
import time

from realindex.core import engine as E
from realindex.core import jobs

FAKE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "fake_worker.py")


def write(root, rel):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(os.urandom(32))


def test_crash_and_hang_fail_only_their_job(root_state, monkeypatch):
    monkeypatch.setattr(jobs, "worker_argv", lambda blender, job_path: [sys.executable, FAKE, job_path])
    root, state = root_state
    for name in ("good_one", "crash_me", "hang_me", "good_two"):
        write(root, "Models/Things/%s.fbx" % name)
    eng = E.Engine(E.RootConfig(root, state), E.Settings(timeout=3.0, inbox_autofile=False))
    pool = jobs.Pool("unused", 4)
    for _ in range(3):
        eng.tick()
    t0 = time.time()
    while time.time() - t0 < 60:
        pool.step([eng])
        if not pool.running and not eng.db.jobs("waiting"):
            break
        time.sleep(0.1)
    st = {a["name"]: a for a in eng.db.assets()}
    assert st["good_one"]["status"] == "ok" and st["good_two"]["status"] == "ok"
    assert st["crash_me"]["status"] == "problem" and "crashed" in st["crash_me"]["problem"]
    assert st["hang_me"]["status"] == "problem" and "Timed out" in st["hang_me"]["problem"]
    attempts = {j["asset_id"]: j["attempts"] for j in eng.db.jobs()}
    assert attempts[st["crash_me"]["id"]] == 2 and attempts[st["hang_me"]["id"]] == 2  # retried once
    assert attempts[st["good_one"]["id"]] == 1
    log = os.listdir(os.path.join(state, "logs"))
    assert any(f.startswith("worker_") for f in log)
    assert not [f for f in os.listdir(os.path.join(state, "jobs")) if f.endswith(".json")]  # cleaned up
