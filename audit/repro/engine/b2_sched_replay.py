"""B1b: a scheduled step fires while a replay is walking the past."""
import pathlib, shutil, sys, tempfile, threading, time
from qanat.project import load
from qanat.store import Store
from qanat.backtest import run_backtest
from qanat.runner import run_job

SRC = pathlib.Path(sys.argv[1])
tmp = pathlib.Path(tempfile.mkdtemp()) / "proj"
shutil.copytree(SRC, tmp)
p, root = load(tmp)
s = Store(p.store_url(root))
print("search_path before:", s.query("SELECT current_setting('search_path') v")["v"][0])

fired = []
def replay():
    run_backtest(s, p, root, "2024-06-01", "2025-06-01", alpha="alpha_momentum", rebalance="5d")
t = threading.Thread(target=replay); t.start()
time.sleep(1.2)
# exactly what Scheduler.fire() does on a cron tick, on the same store object
print("search_path DURING replay:", s.query("SELECT current_setting('search_path') v")["v"][0])
r = run_job(s, p, root, "risk")     # an unrelated scheduled step
print("scheduled step 'risk' during replay -> status", r.status, "rows", r.rows)
t.join()
print("after replay: features.risk rows =",
      int(s.query('SELECT count(*) c FROM features__risk')["c"][0]))
print("(risk is not in the replay's dependency set, so nothing restores what it wrote)")
