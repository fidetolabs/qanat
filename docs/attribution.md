# Which part of it earned the money

A backtest answers one question: did this strategy make money over this window. It does not
answer the question anyone actually has, which is *which part of it did*.

A run that starts from a news story and ends in a weights table has two claims inside it. One is
about the world — chip export rules will lift domestic substitute makers. The other is arithmetic
— hold the top decile of six-month momentum, weighted by the inverse of price to earnings. The
replay tests the arithmetic. The person reads the result as a verdict on the world.

Usually it is not. Momentum and value pay on most groups of stocks, so the formula can return
`+14%` on semiconductors for reasons that have nothing to do with export rules. The number is
honest and the credit goes to the wrong claim, which is worse than no number at all: a strategy
that cannot be tested stalls, and one that passes for the wrong reason gets funded.

Separating the two is not extra machinery. It is a consequence of where each one already lives.

## The idea lives in the universe, the arithmetic lives in the step

A **universe** is the list a portfolio is allowed to hold, point-in-time when its file carries
`from` and `to` — see *Point-in-time universes* in [backtest.md](backtest.md). A **step** points at
one by name and picks from inside it.

```
universe  ->  these 300 names are holdable on this date
step      ->  of those, hold these 20, at these weights
```

So a hypothesis that names a sector, a theme, or a supply chain is a statement about **which
universe**. Everything the hypothesis claims is spent the moment the universe is written down, and
nothing downstream needs to know where it came from.

That is what makes it measurable. A universe is already a swappable argument: `run_backtest` takes
one, it moves the run's digest, and the result carries `universe overridden to 'X' for this run`.
Swap it, hold everything else, and the difference between the two nets is what the idea about the
world was worth.

## An agent writing a pipeline must write a universe, never a list in the script

The failure is quiet and it is the one to design against. Given a news story, the obvious thing for
an agent to emit is the filter inline:

```python
df = df[df.symbol.isin(["NVDA", "TSM", "AMD", "ASML"])]     # no
```

Two separate things break.

**The names are today's.** A list chosen now, applied to 2016, holds the companies that turned out
to win and omits the ones that went bust. That is survivorship bias, and here it lands in the worst
possible place — the profit it invents is attributed to the idea about the world, which is the exact
quantity the comparison exists to measure. A universe csv with `from` and `to` cannot do this;
`ctx.universe()` returns the members of the deciding day and `qanat check` warns when the dates are
missing.

**The idea stops being swappable.** A filter inside a script is invisible to the universe override,
so the two runs the comparison needs are the same run. The hypothesis is still in there, but it can
no longer be turned off, which means it can no longer be priced.

The rule, then: whatever the hypothesis says about which names, goes in a `Universe` with dates, and
the step says `universe:`. An agent that hardcodes symbols has produced a number nobody can
decompose.

## The attribution grid is gone, with the benchmark

The rest of this page described four runs that split a result between the pool and the rule, read
off `benchmark_net` and `excess_net`.

Both were removed in 0.4.0, with everything else that priced a selection as a portfolio. Attributing
a return between two causes is a question about a portfolio, and this is a screening platform: it
reports which companies passed and what each one did, and never adds them together. Adding them up
means choosing how much of each to hold, and that choice is the user's and is made elsewhere.

**What survives is everything above this line.** A universe is still where the idea lives, a step is
still where the arithmetic lives, and an agent still writes a universe rather than a list inside a
script. Those were never about the benchmark.
