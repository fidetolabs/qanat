"""D1: unbounded as-of grid. How big can `dates()` get before anything stops it?"""
import resource, time
from qanat.backtest import dates, BacktestError

for frm, to, every in [("2020-01-01", "2020-01-02", "1s"),
                       ("2020-01-01", "2021-01-01", "1s")]:
    t = time.time()
    try:
        out = dates(frm, to, every)
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6
        print(f"{frm}..{to} every {every}: {len(out):,} stops in {time.time()-t:.1f}s, peak RSS {rss:.0f} MB")
    except BacktestError as exc:
        print(f"{frm}..{to} every {every}: refused -> {exc}")
    except MemoryError:
        print(f"{frm}..{to} every {every}: MemoryError")
