"""E2 redo: put the zero on the exact entry day the scorer picks."""
import pathlib, shutil, sys, tempfile
import numpy as np, pandas as pd
from qanat.project import load
from qanat.store import Store
from qanat.backtest import _price_frame, _price_at, score_period

SRC = pathlib.Path(sys.argv[1])
tmp = pathlib.Path(tempfile.mkdtemp()) / "proj"; shutil.copytree(SRC, tmp)
p, root = load(tmp); s = Store(p.store_url(root))
prices = _price_frame(s, p)
stop, nxt = str(prices.index[10]), str(prices.index[15])
d0, _ = _price_at(prices, pd.Timestamp(stop))
d1, _ = _price_at(prices, pd.Timestamp(nxt))
sym = list(prices.columns)[0]
for label, val in [("0.0 (bad tick / delisting written as zero)", 0.0),
                   ("-1.0 (negative price, e.g. a futures settle)", -1.0)]:
    px = prices.copy(); px.loc[d0, sym] = val
    w = pd.Series({sym: 1.0})
    per, notes = score_period(px, w, pd.Series(dtype=float), stop, nxt, p)
    print(f"  entry price {label}")
    print(f"    gross={per.gross}  net={per.net}  finite={np.isfinite(per.net)}  notes={notes}")
