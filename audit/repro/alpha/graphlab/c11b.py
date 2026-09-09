from common import *
from qanat.project import load
from qanat.store import Store
p, r = load(BASE/"graphlab/c11")
store = Store(p.store_url(r))
for step_id, ref in p.alphas:
    try:
        res = backtest(store, p, r, "2024-01-05", "2024-02-09", alpha=step_id)
        print(f"  {step_id:9s} -> {ref}: sharpe={getattr(res,'sharpe',None)} ret={getattr(res,'total_return',None)}")
    except Exception as e:
        print(f"  {step_id}: {type(e).__name__}: {e}")
print("  pnl tables:", [t for t in store.all_tables() if t.startswith("pnl.")])
print("  alpha_book:", store.alpha_book())
