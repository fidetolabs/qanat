"""B1: what the console sees during a replay, and what two replays do to each other."""
import pathlib, shutil, sys, tempfile, threading, time
from qanat.project import load
from qanat.store import Store
from qanat.backtest import run_backtest, BacktestError

SRC = pathlib.Path(sys.argv[1])
tmp = pathlib.Path(tempfile.mkdtemp()) / "proj"
shutil.copytree(SRC, tmp)
p, root = load(tmp)
s = Store(p.store_url(root))

def rows(t):
    try: return int(s.query(f'SELECT count(*) c FROM "{t}"')["c"][0])
    except Exception as e: return f"ERR:{type(e).__name__}"

print("== observability during a replay (what /api/table would report) ==")
print("  before: normalized.prices =", rows("normalized__prices"))
done = threading.Event()
def replay():
    try:
        run_backtest(s, p, root, "2024-06-01", "2025-06-01", alpha="alpha_momentum", rebalance="5d")
    except Exception as exc:
        print("  replay A raised:", type(exc).__name__, str(exc)[:120])
    finally:
        done.set()
t = threading.Thread(target=replay); t.start()
seen = []
for _ in range(14):
    time.sleep(0.35)
    seen.append(rows("normalized__prices"))
    if done.is_set(): break
print("  during: normalized.prices sampled ->", seen)

print("\n== two replays at once on one store (scheduler live pass vs console run) ==")
errs = []
def racer(name, alpha):
    try:
        r = run_backtest(s, p, root, "2024-06-01", "2025-01-01", alpha=alpha, rebalance="5d")
        print(f"  {name}: status ok, periods={len(r.periods)}, net={r.totals.get('net')}, failures={len(r.failures)}")
    except Exception as exc:
        errs.append(f"{name}: {type(exc).__name__}: {str(exc)[:140]}")
done.wait(120); t.join()
th = [threading.Thread(target=racer, args=(f"replay-{i}", a))
      for i, a in enumerate(["alpha_momentum", "alpha_low_vol"])]
[x.start() for x in th]; [x.join() for x in th]
for e in errs: print("  ERROR", e)
print("  after: normalized.prices =", rows("normalized__prices"))
print("  pit views left:", int(s.query("SELECT count(*) c FROM duckdb_views() WHERE schema_name='qanat_pit'")["c"][0]))
print("  store.as_of after both:", s.as_of)
