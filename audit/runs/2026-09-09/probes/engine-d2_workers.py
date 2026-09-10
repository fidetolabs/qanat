"""D2: a step that never returns. What does the scheduler do?"""
import pathlib, shutil, sys, tempfile, time
from qanat.project import load
from qanat.store import Store
from qanat.scheduler import Scheduler

SRC = pathlib.Path(sys.argv[1])
tmp = pathlib.Path(tempfile.mkdtemp()) / "proj"; shutil.copytree(SRC, tmp)
p, root = load(tmp); s = Store(p.store_url(root))
(root / "steps/tone.sql").unlink(missing_ok=True)
(root / "steps/tone.py").write_text("import time\ndef run(ctx):\n    time.sleep(600)\n")
for st in p.steps:
    if st.id == "tone":
        st.script = "steps/tone.py"
sched = Scheduler(s, p, root, workers=4)
for i in range(6):
    sched.fire("tone")          # a job on a */1 cron, ticking
    time.sleep(0.3)
print("  in-flight:", sched._inflight)
print("  quiet(2s)?", sched.quiet(2.0))
warns = [e["message"] for e in s.recent_events(20) if e["job_id"] == "tone"]
print("  events:", warns[:4])
print("  run rows still 'running':",
      int(s.query("SELECT count(*) c FROM _qanat_runs WHERE status='running'")["c"][0]))
print("  -> no timeout anywhere: the job runs until the process is killed")
