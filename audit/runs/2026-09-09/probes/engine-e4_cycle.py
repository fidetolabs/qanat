"""E4: a cycle inside one features stage. Does `check` catch it? Does run_all?"""
import tempfile, pathlib, textwrap
from qanat.project import load, validate
from qanat.runner import order

tmp = pathlib.Path(tempfile.mkdtemp())
(tmp / "steps").mkdir()
(tmp / "steps/a.sql").write_text("SELECT * FROM features__b")
(tmp / "steps/b.sql").write_text("SELECT * FROM features__a")
(tmp / "steps/w.sql").write_text("SELECT * FROM features__a")
(tmp / "qanat.yaml").write_text(textwrap.dedent("""
    project: cyc
    store: ./data/q.duckdb
    stages:
      - {id: raw, kind: raw}
      - {id: features, kind: features}
      - {id: weights, kind: weights}
    sources:
      - {id: seed, to: [raw.bars], connector: synthetic}
    steps:
      - {id: mk_a, from: [features.b], to: [features.a], script: steps/a.sql}
      - {id: mk_b, from: [features.a], to: [features.b], script: steps/b.sql}
      - {id: alpha_x, from: [features.a], to: [weights.x], script: steps/w.sql}
"""))
p, root = load(tmp)
rep = validate(p, root)
print("check ok? ", rep.ok)
print("errors:   ", rep.errors)
print("warnings: ", rep.warnings)
print("run order:", [s.id for s in order(p)])
print("waiting_on(features.a) ->", [s.id for s in p.waiting_on(["features.a"])])
