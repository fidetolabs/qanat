"""E2/E5/E7: money-critical edge cases -- bad prices, silent NaN, missing clock."""
import json, pathlib, shutil, sys, tempfile
import numpy as np, pandas as pd
from fastapi.testclient import TestClient
from qanat.api import AppState, create_app
from qanat.project import load
from qanat.store import Store
from qanat.runner import run_job
from qanat.backtest import run_backtest, _price_frame, score_period, Costs

SRC = pathlib.Path(sys.argv[1])
def fresh():
    tmp = pathlib.Path(tempfile.mkdtemp()) / "proj"
    shutil.copytree(SRC, tmp)
    p, root = load(tmp)
    return p, root, Store(p.store_url(root))

print("== E2: a zero price in the price table ==")
p, root, s = fresh()
prices = _price_frame(s, p)
syms = list(prices.columns)[:2]
d0, d1 = prices.index[10], prices.index[11]
prices.loc[d0, syms[0]] = 0.0            # a bad tick, or a delisting written as 0
w = pd.Series({syms[0]: 0.5, syms[1]: 0.5})
per, notes = score_period(prices, w, pd.Series(dtype=float), str(d0), str(d1), p)
print("  gross:", per.gross, "| net:", per.net, "| notes:", notes)
print("  finite?", np.isfinite(per.net), " -> an infinite return is reported as a real number"
      if not np.isfinite(per.net) else "")

print("\n== E5: weights that are NaN never trip the |w| = 1 check ==")
p, root, s = fresh()
(root / "steps/alpha_momentum.py").write_text(
    "import pandas as pd, numpy as np\n"
    "def run(ctx):\n"
    "    px = ctx.read('normalized.prices')\n"
    "    syms = sorted(px['symbol'].unique())[:3]\n"
    "    return pd.DataFrame({'symbol': syms, 'weight': [np.nan]*len(syms),\n"
    "                         'date': [px['date'].max()]*len(syms)})\n")
r = run_job(s, p, root, "alpha_momentum")
print("  step:", r.status, "rows", r.rows)
evs = [e for e in s.recent_events(20) if e["job_id"] == "alpha_momentum"]
print("  events:", [(e["level"], e["message"][:70]) for e in evs][:3])
try:
    res = run_backtest(s, p, root, "2025-01-01", "2025-06-01", alpha="alpha_momentum", rebalance="5d")
    print("  backtest totals:", {k: res.totals.get(k) for k in ("periods", "net", "gross")})
    print("  report JSON contains a bare NaN token:", "NaN" in json.dumps(res.as_dict(), default=str))
except Exception as exc:
    print("  backtest raised:", type(exc).__name__, str(exc)[:120])

app = create_app(AppState(store=s, project=p, root=root, sched=None))
c = TestClient(app)
row = s.backtests(1)
if row:
    resp = c.get(f"/api/backtests/{row[0]['run_id']}")
    body = resp.text
    print("  HTTP body has NaN token:", "NaN" in body, "| status", resp.status_code)
    try:
        json.loads(body, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        print("  strict JSON parse (what a browser does): ok")
    except ValueError as e:
        print("  strict JSON parse (what a browser does): FAILS on", e)

print("\n== E7: lookahead guard on a table with no time column ==")
p, root, s = fresh()
(root / "steps/momentum.py").write_text(
    "import pandas as pd\n"
    "def run(ctx):\n"
    "    return pd.DataFrame({'symbol': ['A'], 'score': [1.0]})\n")   # no date column
from qanat.runner import run_step
st = p.job("momentum")
r = run_step(s, p, root, st, as_of="2024-01-01")
print("  step written at as_of=2024-01-01 with no clock ->", r.status)
print("  time column found:", s.time_column("features.momentum"))
print("  -> the lookahead check silently skips any table without a recognised time column")
