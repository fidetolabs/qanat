"""A1: SIGKILL a process in the middle of a replay. What is left in the store?"""
import os, pathlib, shutil, signal, subprocess, sys, tempfile, time

SRC = pathlib.Path(sys.argv[1])
tmp = pathlib.Path(tempfile.mkdtemp()) / "proj"
shutil.copytree(SRC, tmp)

worker = tmp / "_replay.py"
worker.write_text(f'''
import pathlib
from qanat.project import load
from qanat.store import Store
from qanat.backtest import run_backtest
p, root = load(pathlib.Path(r"{tmp}"))
s = Store(p.store_url(root))
print("rows before:", s.query('SELECT count(*) c FROM normalized__prices')["c"][0], flush=True)
run_backtest(s, p, root, "2024-06-01", "2026-06-01", alpha="alpha_momentum", rebalance="1d")
''')

proc = subprocess.Popen([sys.executable, str(worker)], stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, env={**os.environ})
time.sleep(3)                      # let it get well into the window
proc.send_signal(signal.SIGKILL)   # power cut
proc.wait()
print("worker said:", (proc.stdout.read() or "").strip()[:200])
print("killed mid-replay\n")

from qanat.project import load
from qanat.store import Store
p, root = load(tmp)
s = Store(p.store_url(root))
print("AFTER THE CRASH")
for t in ["raw.daily_prices", "normalized.prices", "features.momentum", "weights.momentum"]:
    try:
        n = s.query(f'SELECT count(*) c FROM "{t.replace(".", "__")}"')["c"][0]
    except Exception as exc:
        n = f"ERR {type(exc).__name__}"
    print(f"  {t:24} rows={n}")
mx = s.max_time("normalized.prices")
print("  newest date in normalized.prices:", mx)
print("  leftover pit schema:",
      s.query("SELECT count(*) c FROM duckdb_views() WHERE schema_name='qanat_pit'")["c"][0], "views")
print("  backtests left 'running':",
      s.query("SELECT count(*) c FROM _qanat_backtests WHERE status='running'")["c"][0])
print("  runs left 'running':",
      s.query("SELECT count(*) c FROM _qanat_runs WHERE status='running'")["c"][0])
print("  backtest rows:", s.query("SELECT run_id,status,periods FROM _qanat_backtests ORDER BY created_at DESC LIMIT 3").to_dict("records"))
