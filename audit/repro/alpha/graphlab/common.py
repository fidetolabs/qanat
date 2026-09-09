import sys, os
sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
from harness import *   # noqa
import pandas as pd

ALPHA = '''
def run(ctx):
    import pandas as pd
    px = ctx.read("normalized.prices")
    last = px[px["date"] == px["date"].max()]
    n = len(last)
    return pd.DataFrame({"date": last["date"].values, "symbol": last["symbol"].values,
                         "weight": [1.0/n]*n})
'''

def bars():
    return flat_then_drift(DAYS[:30], ["AAA","BBB","CCC"], 0.001)

def show(results):
    for r in results:
        print(f"  {r.job_id:16s} {r.status:7s} rows={r.rows}" + (f"  ERR={r.error}" if r.error else ""))

def rep_str(rep):
    return ("ERRORS:\n   " + "\n   ".join(rep.errors) if rep.errors else "ERRORS: none") + \
           ("\nWARNINGS:\n   " + "\n   ".join(rep.warnings) if rep.warnings else "\nWARNINGS: none")
