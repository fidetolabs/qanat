"""E2 e2e: one zero price in the store -> what comes out of the API."""
import json, pathlib, shutil, sys, tempfile
from fastapi.testclient import TestClient
from qanat.api import AppState, create_app
from qanat.project import load
from qanat.store import Store
from qanat.backtest import run_backtest

SRC = pathlib.Path(sys.argv[1])
tmp = pathlib.Path(tempfile.mkdtemp()) / "proj"; shutil.copytree(SRC, tmp)
p, root = load(tmp); s = Store(p.store_url(root))

# a single bad tick, the kind a real feed prints once a year
s.query("SELECT 1")
row = s.query('SELECT ts, symbol FROM raw__daily_prices ORDER BY ts LIMIT 1 OFFSET 1200').to_dict("records")[0]
with s._lock:
    s.con.execute('UPDATE raw__daily_prices SET close = 0 WHERE ts = ? AND symbol = ?',
                  [row["ts"], row["symbol"]])
span = s.query("SELECT min(ts) a, max(ts) b FROM raw__daily_prices").to_dict("records")[0]
FRM, TO = str(span["a"])[:10], str(span["b"])[:10]
print("  data span", FRM, TO)
print("  zeroed close for", row["symbol"], "on", row["ts"])
from qanat.runner import run_job
run_job(s, p, root, "normalize")

res = run_backtest(s, p, root, FRM, TO, alpha="alpha_momentum", rebalance="5d")
print("  periods:", len(res.periods), "net:", res.totals.get("net"), "equity:", res.totals.get("equity"))
print("  any non-finite period net:", [p_.net for p_ in res.periods if p_.net in (float('inf'), float('-inf')) or p_.net != p_.net][:3])
print("  notes mentioning the bad price:", [n for n in res.notes if "0" in n][:2] or "none")

app = create_app(AppState(store=s, project=p, root=root, sched=None))
c = TestClient(app)
r = c.get(f"/api/backtests/{res.run_id}")
print("  HTTP status", r.status_code, "| body has Infinity token:", "Infinity" in r.text)
try:
    json.loads(r.text, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    print("  browser-strict JSON parse: ok")
except ValueError as e:
    print("  browser-strict JSON parse: FAILS on", e, "-> the console page cannot render this run")
b = c.get("/api/alphas")
print("  /api/alphas status", b.status_code, "| Infinity in body:", "Infinity" in b.text)
