"""THE WHOLE CHAIN on real public data:
   enroll sources -> add steps -> build tables -> alpha -> backtest -> PnL table.

Prices: ECB reference rates via Frankfurter (real).
Feature: Korean public holidays via Nager.Date (real, non-price, unicode).
Hypothesis: hold the currency that fell most against EUR over 5 days; sit flat
            in the week of a Korean public holiday.
"""
import shutil, pathlib
import pandas as pd
from qanat.project import load, validate
from qanat.runner import run_all
from qanat.store import Store
from qanat.backtest import run_backtest

ROOT = pathlib.Path("/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/weblab/chain")
if ROOT.exists(): shutil.rmtree(ROOT)
(ROOT / "steps").mkdir(parents=True); (ROOT / "universes").mkdir()

pd.DataFrame({"symbol": ["USD", "KRW", "JPY"]}).to_csv(ROOT / "universes/ccy.csv", index=False)

(ROOT / "qanat.yaml").write_text("""project: worldfx
store: ./data/q.duckdb
universes:
  - {id: ccy, index: EURXXX, symbols: ./universes/ccy.csv}
stages:
  - {id: raw, kind: raw, description: landed from the public web}
  - {id: normalized, kind: features}
  - {id: features, kind: features}
  - {id: weights, kind: weights}
  - {id: pnl, kind: pnl}
sources:
  - id: fx
    to: [raw.fx]
    connector: rest
    mode: replace
    options:
      url: https://api.frankfurter.dev/v1/2024-01-01..2024-12-31
      params: {base: EUR, symbols: "USD,KRW,JPY"}
      records: rates
      orient: index
      index_column: date
  - id: holidays
    to: [raw.holidays]
    connector: rest
    mode: replace
    options:
      url: https://date.nager.at/api/v3/PublicHolidays/2024/KR
steps:
  - id: normalize_fx
    from: [raw.fx]
    to: [normalized.rates]
    script: steps/normalize_fx.sql
  - id: feat_holiday
    from: [raw.holidays]
    to: [features.holiday]
    script: steps/feat_holiday.sql
  - id: alpha_reversal
    from: [normalized.rates, features.holiday]
    to: [weights.reversal]
    script: steps/alpha_reversal.py
    universe: ccy
    options: {lookback: 5}
backtest:
  prices: normalized.rates
  price_column: close
  symbol_column: symbol
  date_column: date
  rebalance: "5d"
  fee_bps: 2.0
  slippage_bps: 1.0
  split: "2024-09-01"
""")

# raw.fx arrives WIDE (date, JPY, KRW, USD) -- one column per currency. Unpivot it.
(ROOT / "steps/normalize_fx.sql").write_text("""
SELECT CAST(date AS DATE) AS date, 'USD' AS symbol, CAST(USD AS DOUBLE) AS close FROM raw__fx
UNION ALL
SELECT CAST(date AS DATE), 'KRW', CAST(KRW AS DOUBLE) FROM raw__fx
UNION ALL
SELECT CAST(date AS DATE), 'JPY', CAST(JPY AS DOUBLE) FROM raw__fx
""")

(ROOT / "steps/feat_holiday.sql").write_text("""
SELECT CAST(date AS DATE) AS date, localName AS name, TRUE AS is_holiday
FROM raw__holidays
""")

(ROOT / "steps/alpha_reversal.py").write_text('''
"""Buy the currency that fell most against EUR; sit flat around KR holidays."""
import pandas as pd

def run(ctx):
    n = int(ctx.options.get("lookback", 5))
    px = ctx.read("normalized.rates")
    hol = ctx.read("features.holiday")
    if px.empty:
        return pd.DataFrame(columns=["symbol", "weight", "as_of"])
    px = px.sort_values("date")
    as_of = px["date"].max()

    # a Korean holiday inside the last 7 days means sit flat
    if not hol.empty:
        recent = hol[(pd.to_datetime(hol["date"]) <= pd.Timestamp(as_of)) &
                     (pd.to_datetime(hol["date"]) > pd.Timestamp(as_of) - pd.Timedelta(days=7))]
        if len(recent):
            ctx.log(f"flat: {recent['name'].iloc[0]} within 7 days of {as_of}")
            return pd.DataFrame(columns=["symbol", "weight", "as_of"])

    rows = []
    for sym, g in px.groupby("symbol"):
        if len(g) <= n:
            continue
        past, now = g["close"].iloc[-n - 1], g["close"].iloc[-1]
        if past and past > 0:
            rows.append({"symbol": sym, "score": now / past - 1.0})
    if not rows:
        return pd.DataFrame(columns=["symbol", "weight", "as_of"])
    allowed = set(ctx.universe()["symbol"])
    d = pd.DataFrame(rows)
    d = d[d["symbol"].isin(allowed)]
    if d.empty:
        return pd.DataFrame(columns=["symbol", "weight", "as_of"])
    # EUR/XXX up means XXX weakened -- buy the weakest, i.e. the largest rise
    pick = d.nlargest(1, "score").copy()
    pick["weight"] = 1.0
    pick["as_of"] = as_of
    return pick[["symbol", "weight", "score", "as_of"]]
''')

print("=" * 92)
print("STEP 1-2 · enroll the sources and hold the file against the contract")
print("=" * 92)
p, r = load(ROOT)
rep = validate(p, r)
print(f"  qanat check: ok={rep.ok}  errors={rep.errors}")
for w in rep.warnings: print(f"    warn: {w}")

store = Store(p.store_url(r))
print()
print("=" * 92)
print("STEP 3 · run the graph: fetch from the web, build every table")
print("=" * 92)
for res in run_all(store, p, r):
    print(f"  {res.job_id:<16} {res.status:<8} rows={res.rows:<6} {(res.error or '')[:56]}")
print()
for ref in sorted(store.all_tables()):
    i = store.table_info(ref)
    print(f"  {ref:<20} {i.rows:>6} rows · clock={store.time_column(ref)}")
print()
print("  normalized.rates (the price table the backtest will use):")
print(store.read("normalized.rates").sort_values(["date","symbol"]).head(6).to_string(index=False))
print()
print("  features.holiday (Korean names, straight off the web):")
print(store.read("features.holiday").head(4).to_string(index=False))

print()
print("=" * 92)
print("STEP 4 · backtest -> PnL")
print("=" * 92)
bt = run_backtest(store, p, r, "2024-02-01", "2024-11-30", alpha="alpha_reversal", seed=1)
t = bt.totals
print(f"  status   : {store.backtest(bt.run_id)['status']}")
print(f"  periods  : {t.get('periods')} · net {t.get('net'):.2%} · turnover {t.get('turnover'):.2f}")
seg = bt.segments
if seg.get("in_sample"):
    print(f"  in sample     net {seg['in_sample'].get('net'):.2%}")
    print(f"  out of sample net {seg['out_of_sample'].get('net'):.2%}")
print(f"  failures : {len(bt.failures)}   notes: {len(bt.notes)}")
for n in bt.notes[:3]: print(f"    note: {n[:86]}")
print()
print("  PnL table written by the replay:")
pnl = store.read("pnl.reversal")
print(f"    pnl.reversal · {len(pnl)} rows · columns {list(pnl.columns)}")
print(pnl[["as_of", "holdings", "gross", "fees", "net", "equity"]].tail(4).to_string(index=False))
print()
print(f"  strategy book: {[(b['alpha'], round(b['last_net'], 4)) for b in store.alpha_book()]}")
store.close()
