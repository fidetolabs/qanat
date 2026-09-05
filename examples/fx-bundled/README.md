# Real data, in the repo

Synthetic prices prove the pipeline runs. They cannot tell you whether an alpha
works, because you get whatever the generator was written to produce.

This project holds 27 years of real numbers instead: one European Central Bank
reference rate per currency per business day, 1999 to 2026, in
`data/ecb_usd_1999_2026.csv.gz`. 126 KB. No key, no account, and no network — it
runs on a plane, in CI, and behind a corporate proxy.

```bash
qanat run
qanat backtest --from 1999-01-04 --to 2026-09-04 --rebalance 20d --split 2015-01-01
```

## It is `examples/fx-real` with the source swapped

Nothing else differs. Same stages, same steps, same universe, same `backtest:`
block. One project fetches over HTTP from `api.frankfurter.dev`, the other reads a
file, and neither knows which.

That is the README's claim about pointing `sources:` at a real feed, written out
as two projects you can diff.

## What it finds

A losing strategy, which is the useful answer:

```
    gross             -1.687%
    fees              -3.485%
    slippage          -6.970%
    ------------------------------
    net              -13.673%

    in sample         +1.874%  293 periods
    out of sample    -15.260%  212 periods
```

Gross is roughly flat. Costs take it to −13.7% over 505 rebalances, and the
out-of-sample half is worse than the half the parameters were chosen on. Both are
what you would expect from a plain momentum rule on major-currency crosses, and
both are the reason net edge is the headline number here rather than gross.

Raise `--fee-bps` until an alpha dies to find out how much of it was ever real.
This one does not have far to fall.

## Where the data came from

`https://api.frankfurter.dev/v1/1999-01-04..?base=USD&symbols=EUR,GBP,JPY,CHF,AUD,CAD`,
pivoted to one row per day. The ECB publishes these rates for public reuse with
attribution; they are the only real dataset qanat ships, and the reason it is this
one rather than equity prices is that most price vendors permit you to fetch their
history but not to hand it on.
