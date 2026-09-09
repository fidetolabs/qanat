"""C2 end-to-end: an injecting table name in qanat.yaml, executed by `qanat run`."""
import pathlib, shutil, sys, tempfile
from qanat.project import load
from qanat.runner import run_job
from qanat.store import Store
from qanat.project_io import save_project
from qanat.models import Step

SRC = pathlib.Path(sys.argv[1])
tmp = pathlib.Path(tempfile.mkdtemp()) / "proj"
shutil.copytree(SRC, tmp)
project, root = load(tmp)
store = Store(project.store_url(root))
print("tables before:", sorted(store.all_tables()))

bad = 'features.z" AS SELECT 1 AS q; DROP TABLE features__momentum; CREATE OR REPLACE TABLE "zzz'
project.steps.append(Step(id="inj", reads=["normalized.prices"], writes=[bad],
                          script="steps/inject.sql"))
(tmp / "steps/inject.sql").write_text("SELECT 1 AS q")
save_project(project, root)
project, root = load(tmp)

res = run_job(store, project, root, "inj")
print("run status:", res.status, "|", (res.error or "")[:90])
print("tables after :", sorted(store.all_tables()))
print("features.momentum survived:", store.exists("features.momentum"))
