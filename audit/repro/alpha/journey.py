"""The whole story, through the console API: connect a source, build the graph,
add an alpha from the shelf, run it, backtest it. Exactly what a new user does."""
import sys, shutil, tempfile, pathlib, json
sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
import pandas as pd
from fastapi.testclient import TestClient
from qanat.api import AppState, create_app
from qanat.project import load
from qanat.store import Store
from qanat.scaffold import write_project
from harness import per_symbol_drift

root = pathlib.Path(tempfile.mkdtemp()) / "journey"
root.mkdir(parents=True)
write_project(root, "journey", force=True)
CAL = pd.date_range("2024-01-01", periods=300, freq="D")
(root / "seed").mkdir(exist_ok=True)
per_symbol_drift(CAL, {"AAA": 0.002, "BBB": 0.0008, "CCC": 0.0015, "DDD": -0.0005}) \
    .to_csv(root / "seed/prices.csv", index=False)

p, r = load(root)
store = Store(p.store_url(r))
c = TestClient(create_app(AppState(store=store, project=p, root=r, sched=None)))
def show(step, resp, keys=None):
    ok = resp.status_code < 400
    body = resp.json() if ok else resp.text[:150]
    if keys and isinstance(body, dict):
        body = {k: body.get(k) for k in keys}
    print(f"  [{resp.status_code}] {step:<38} {str(body)[:110]}")
    return ok

print("=" * 92)
print("THE JOURNEY  ·  every step through the console API, nothing done by hand")
print("=" * 92)
print("1. what does a fresh project look like?")
g = c.get("/api/graph").json()
print(f"   stages {[s['id'] for s in g['stages']]} · tables {len(g['tables'])} · jobs {len(g['jobs'])}")
print(f"   health: {g['health']}")

print("\n2. connect a data source")
show("POST /api/sources (csv)", c.post("/api/sources", json={
    "id": "prices", "to": ["raw.prices"], "connector": "csv", "mode": "replace",
    "options": {"path": "./seed/prices.csv"}}))

print("\n3. build the graph")
show("POST /api/steps (normalize)", c.post("/api/steps", json={
    "id": "norm", "from": ["raw.prices"], "to": ["normalized.prices"],
    "script": "steps/norm.sql"}))
(root / "steps/norm.sql").write_text(
    "SELECT CAST(date AS DATE) AS date, symbol, CAST(close AS DOUBLE) AS close FROM raw__prices")
show("GET  /api/check", c.get("/api/check"), ["ok", "errors"])

print("\n4. add an alpha from the shelf")
show("GET  /api/shelf", c.get("/api/shelf"), ["reads"])
show("POST /api/alphas (momentum)", c.post("/api/alphas", json={
    "name": "mom", "reads": "normalized.prices", "shelf": "momentum",
    "options": {"lookback": 20, "top_n": 2}}), ["id", "writes", "script"])

print("\n5. run the pipeline")
from qanat.runner import run_all
p2, r2 = load(root)
res = run_all(store, p2, r2)
print("   ", [(x.job_id, x.status, x.rows) for x in res])
for x in res:
    if not x.ok: print(f"   FAILED {x.job_id}: {x.error[:130]}")

print("\n6. what can we backtest?")
show("GET /api/backtest/conditions", c.get("/api/backtest/conditions"), ["alphas", "data"])

print("\n7. backtest it")
st = AppState(store=store, project=p2, root=r2, sched=None)
c2 = TestClient(create_app(st))
resp = c2.post("/api/backtest", json={"from": "2024-03-01", "to": "2024-09-01",
                                      "rebalance": "5d", "alpha": "alpha_mom", "seed": 1})
print(f"   HTTP {resp.status_code}")
if resp.status_code < 400:
    b = resp.json()
    print(f"   totals   : {b['totals']}")
    print(f"   periods  : {len(b['periods'])}")
    print(f"   failures : {len(b['failures'])}")
    if b['failures']: print(f"     -> {b['failures'][0][:120]}")
    print(f"   notes    : {b['notes'][:1]}")
    row = store.backtest(b['run_id'])
    print(f"   status recorded in the store: {row['status']}")
else:
    print(f"   {resp.text[:250]}")

print("\n8. does the book show it?")
book = c2.get("/api/alphas").json()
for row in book["alphas"]:
    print(f"   {row['name']:<12} runs {row['runs']} · last_net {row['last_net']} · pnl {row['pnl']}")
print(f"   stats: {json.dumps(book['stats'], default=str)[:130]}")
store.close()
