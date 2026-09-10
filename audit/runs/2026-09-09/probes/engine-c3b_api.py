"""C3b: isolated API faults, each on a fresh copy of the project."""
import pathlib, shutil, tempfile, sys
from fastapi.testclient import TestClient
from qanat.api import AppState, create_app
from qanat.project import load
from qanat.store import Store

SRC = pathlib.Path(sys.argv[1])

def fresh():
    tmp = pathlib.Path(tempfile.mkdtemp()) / "proj"
    shutil.copytree(SRC, tmp)
    project, root = load(tmp)
    store = Store(project.store_url(root))
    return TestClient(create_app(AppState(store=store, project=project, root=root, sched=None))), tmp

def show(l, r): print(f"  [{r.status_code}] {l}: {r.text[:200]}".replace("\n", " "))

print("== C3: path traversal, VALID step (so the request succeeds) ==")
c, tmp = fresh()
out = tmp.parent / "OUTSIDE"; out.mkdir(exist_ok=True)
show("script ../OUTSIDE/pwned.py", c.post("/api/steps", json={
    "id": "evil", "from": ["normalized.prices"], "to": ["features.evil"],
    "script": "../OUTSIDE/pwned.py"}))
print("   wrote outside root:", (out / "pwned.py").is_file())
show("run it", c.post("/api/jobs/evil/run"))

print("\n== C2b: injecting table name, no dots in payload ==")
c, tmp = fresh()
bad = 'features.z" AS SELECT 1 AS q; DROP TABLE main__gone; CREATE OR REPLACE TABLE "main"'
bad = 'features.z" AS SELECT 1 AS q; DROP TABLE features__momentum; CREATE OR REPLACE TABLE "zzz'
show("to=" + bad[:40], c.post("/api/steps", json={
    "id": "inj", "from": ["normalized.prices"], "to": [bad], "script": "steps/momentum.py"}))

print("\n== POISON: does a rejected edit corrupt in-memory state? ==")
c, tmp = fresh()
show("health before", c.get("/api/check"))
show("bad step (unknown upstream)", c.post("/api/steps", json={
    "id": "bogus", "from": ["nope.nothing"], "to": ["features.b1"], "script": "steps/momentum.py"}))
show("check AFTER the rejected edit", c.get("/api/check"))
show("unrelated valid edit now", c.put("/api/store", json={"store": "./data/qanat.duckdb"}))
show("delete an unrelated step now", c.delete("/api/steps/tone"))
print("   qanat.yaml on disk still mentions 'bogus'? ",
      "bogus" in (tmp / "qanat.yaml").read_text())

print("\n== C4: store repointed outside the project ==")
c, tmp = fresh()
show("store=/tmp/qanat_elsewhere.duckdb", c.put("/api/store", json={"store": "/tmp/qanat_elsewhere.duckdb"}))
print("   file created:", pathlib.Path("/tmp/qanat_elsewhere.duckdb").exists())
