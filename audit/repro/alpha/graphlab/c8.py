from common import *
from qanat.project import load, validate
import yaml as Y

def opt_case(tag, sql, options):
    S = "  - id: filt\n    from: [normalized.prices]\n    to: [normalized.filt]\n    script: steps/filt.sql\n"
    if options is not None:
        S += "    options: " + Y.safe_dump(options, default_flow_style=True).strip() + "\n"
    root = build(f"graphlab/c8_{tag}", bars(), ALPHA, extra_steps=S)
    (root/"steps/filt.sql").write_text(sql)
    p,r = load(root)
    print(f"--- {tag} ---  options={options}")
    print("   check:", validate(p,r).errors or "none")
    p,r,store,rep,res = run_pipeline(root)
    for x in res:
        if x.job_id=="filt": print("   run:", x.status, "|", (x.error or "")[:230])
    i = store.table_info("normalized.filt")
    print("   normalized.filt:", (f"{i.rows} rows" if i else "MISSING"))
    print("   tables now:", store.all_tables())
    store.con.close()

SQL = "SELECT * FROM normalized__prices WHERE symbol = '${sym}'\n"
opt_case("ok",      SQL, {"sym": "AAA"})
opt_case("missing", SQL, {"other": "AAA"})
opt_case("noopts",  SQL, None)
opt_case("quote",   SQL, {"sym": "AAA' OR 1=1 --"})
opt_case("semi",    SQL, {"sym": "x'; DROP TABLE normalized__prices; --"})
opt_case("numeric", "SELECT *, ${n} AS k FROM normalized__prices\n", {"n": "1 UNION ALL SELECT 9"})
