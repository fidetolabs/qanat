# Forty real 10-K filings

Eight companies, five years each, downloaded from SEC EDGAR on the first run.
Nothing here is invented and nothing is bundled.

| Group | Companies |
| --- | --- |
| Big tech | Apple, Microsoft, Alphabet, Meta, NVIDIA |
| Retail | Walmart, Target, Costco |

Two groups that are each internally comparable and not comparable with each
other. That is on purpose: a relative screen only means something inside a
peer group, and the SIC code rides along in `source_keys` so a query can say
which group it is asking about.

## Run it

```bash
pip install 'qanat-fdtl[prices]'                    # the prices extra brings yfinance
export SEC_USER_AGENT="your name you@example.com"   # the SEC refuses anonymous requests
qanat check
qanat run            # about ten minutes the first time, seconds after that
```

The documents are cached under `data/documents` as gzip. A filing never changes
once it is accepted -- an amendment gets a new accession number -- so the cache
never expires and a second run only looks for filings it has not seen.

Then hand it to an agent:

```bash
qanat mcp --scope research
```

## What you get

| Table | Rows | One row is |
| --- | --- | --- |
| `raw.documents` | 40 | one filing: who, which form, when it was published, where the body is |
| `raw.prices` | 11,632 | one close, printed and adjusted, with which is which recorded |
| `normalized.facts` | 43,104 | one tagged number, with its period, its dimensions and its unit |
| `normalized.filing_prices` | 40 | one close per filing, on the day it became public |
| `normalized.spans` | 904 | one Item of one document, with the character range it occupies |

## Screening it

```bash
qanat screen --filter '[{"metric":"pe","op":"<","value":30},
                        {"metric":"net_margin","op":">","value":0.15}]' \
             --from 2023-12-31 --to 2025-12-31
```

```
2023-12-31  4 of 8 passed
  · 0000320193   +26.2% to 2024-11-01
  · 0001045810  +189.8% to 2024-02-21
  · 0001326801  +151.6% to 2024-02-02
  · 0001652044   +33.7% to 2024-01-31
```

Each company is priced on **its own** filing dates, not a shared calendar.
Nothing is added up: adding those returns together needs a decision about how
much of each to hold, and that decision is yours.

## Two metrics, and why they are a warning

`pe` and `net_margin` are defined in `qanat.yaml`. Both got two things wrong
first time, and both failures are the reason the metric map is the large piece
of work left:

**A 10-K reports three years, all stamped with the same filing date.** Ordering
by `as_of` alone picks an arbitrary one of the three. Meta's PE came out at 80
instead of 28.8 until the tiebreak on `period_end` went in.

**Filers do not agree on a revenue tag.** NVIDIA uses `Revenues`; Apple uses
`RevenueFromContractWithCustomerExcludingAssessedTax`. Taking one and not the
other dropped NVIDIA's revenue and produced a **271% net margin**, which is the
useful kind of wrong — impossible, so you notice. A tag that is merely the
wrong one of two plausible tags would not have announced itself.

## The two dates

This is what the example is for.

A company publishes its results weeks after the period they cover. Across these
forty filings the gap runs from **24 to 47 days**:

| | AAPL | COST | GOOGL | META | MSFT | NVDA | TGT | WMT |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| days from period end to filing | 34 | 38 | 31-36 | 29-34 | 27-30 | 24-47 | 39 | 41-46 |

So `time_columns` names `published_at`, never `period_end`. Ask what Costco
looked like in September 2025 and you get the 2024 numbers, because the 2025
ones were not public until 8 October. Point the clock at `period_end` instead
and every replay reads the future.

The fiscal years are deliberately all over the calendar -- January, February,
June, August, September, December -- because a pipeline that only ever sees
31 December year-ends never has to get this right.

## Two things worth knowing

**Nothing reads `facts` or `spans`, and `qanat check` says so.** That warning is
correct. There is no metrics layer and no results layer here: a metric is a
query over the facts and a screen is a call that returns a list. The pipeline's
job ends when the facts are clean and dated.

**Three prices, and the one you want depends on the question.**
`normalized.filing_prices` carries `close_as_printed`, `close` and
`adjusted_close`. Yahoo rewrites old prices after a split, so neither of its
own columns is what traded that day: NVIDIA's 2021 close comes back as $13.11
against the $524.54 that was really on the screen.

A ratio against a filing figure needs **`close_as_printed`**, because the EPS
comes from a filing and the SEC never rewrites one. Six of these 40 filings
had the wrong PE before this was fixed — Alphabet's 2022 PE read **1.3**
instead of 26.4, which would have made it the cheapest company on the list.

A return needs **`adjusted_close`**, because a split is not a loss.

**Prices come from Yahoo and may not be sold from.** `yfinance` reads Yahoo's
endpoints and Yahoo's terms do not grant redistribution, so every row carries
`source: yfinance`. That column is what a later cleanup acts on.

**`spans.rule` says how each boundary was decided.** A 10-K names every Item on
its contents page and again throughout the prose, so most matches for
"Item 1A" are not the heading. Section detection gets all 904 of these right,
and it is still a heuristic on documents it has not seen. The rule travels with
the row so a caller can distrust it.

## Coverage

24 of the 40 filings carry all 23 Items. Every gap in the other 16 is `Item 1C`,
Cybersecurity, which the SEC only required for fiscal years ending on or after
15 December 2023 -- so those filings genuinely do not have one. There are no
sections that exist in a document and were missed.
