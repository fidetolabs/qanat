# The chaos audit

Breaking qanat on purpose, on an interval, and keeping what that teaches.

The reason this is a folder and not a one-off document: the value of an audit is
not the findings, it is the **catalogue**. A run that has to re-check what the last
run already covered spends its time proving things that were already true. So every
run promotes what it learns —

| what a run produces | where it goes | who runs it after that |
| --- | --- | --- |
| a finding | a case in `tests/test_audit_fixes.py` | CI, on every push |
| a discriminator | a suite in `batteries/`, **over real data** | on demand, and before a release |
| a lesson | a line in `SCENARIOS.md` | the next person, by reading |

— and the next run starts on new ground.

## The folders

```
batteries/    standing suites, re-run every audit. Their job is to notice a
              regression in ground that was already covered. **Over real data,
              never generated** -- see below.
runs/         one folder per audit: what was tried, what was found, the report.
FINDINGS.md   the ledger. Everything ever found, and where it stands.
SCENARIOS.md  what to try, and what each thing has caught.
```

## A battery runs on real data, or it does not run

**There was a `lab/` here that built a whole project out of generated prices** --
a fixed daily drift, a few invented symbols -- and two suites over it:
`test_arithmetic.py` checked that a forward return came back as the rate the
generator used, and `test_leak.py` checked that a step could not read past the
as-of date.

**They went on 2026-11-09, with the `lab/` underneath them.** A constant drift is
not a filing. The arithmetic battery was asking whether DuckDB can divide, and the
leak battery proved its boundary against a calendar nobody files on, so neither one
could fail for a reason that would also happen to a real user.

**`test_websources.py` was rewritten the same day, and it had to be.** Six of its
seven tests reached the web through the `rest` connector and the seventh through
`csv`, both deleted in 0.12.0 -- so the whole file was dead, and the way it died
is the part worth keeping:

```python
if any(w in str(exc).lower() for w in ("timeout", "connect", "temporarily", ...)):
    pytest.skip(f"the host is not answering right now: {exc}")
```

`unknown connector 'rest'` contains `connect`. Six tests reported themselves as a
host outage, and CI printed a green tick over a job that ran nothing. **The guard
is typed now** -- a transport error or a 5xx skips, a 4xx raises -- because a 403
from EDGAR is this product being wrong and has to go red.

It runs against the two connectors that exist, on quirks each one's own docstring
names: a CIK keeps the zeros that are part of it, a frequent filer's older 10-Ks
are only on the paginated pages, a filing is dated after the period it covers, a
split is recorded beside the price it restated, and a currency is read from the
feed rather than guessed from the symbol.

**What a battery needs to run, and what it does when it cannot.** EDGAR refuses a
request with no contact, so those three skip unless `SEC_USER_AGENT` is set -- in
CI, from a repository secret. `yfinance` is the `prices` extra, so the two price
batteries `importorskip`. Both skips say which one is missing. **Neither is ever
spelled as a host being down.**

The frozen probes under `runs/2026-09-09/` import the deleted `lab/` and no longer
run. They stay as the record of what that audit did, which is what `runs/` is for.

## Running one

```bash
uv run pytest audit/batteries -m audit                     # start here
uv run pytest audit/batteries -m "audit and not network"   # offline
```

Green batteries mean the covered ground is still covered, and the run can be spent
on `SCENARIOS.md` instead. Anything new goes in `runs/<date>/`.

## The runs so far

| run | what it covered | findings | report |
| --- | --- | --- | --- |
| [2026-09-09](runs/2026-09-09/) | the engine, the console, and the workflow from a source to a PnL table | 73 | [engine](runs/2026-09-09/report-01-engine.html) · [console](runs/2026-09-09/report-02-console.html) · [workflow](runs/2026-09-09/report-03-workflow.html) |

Published versions, which is what to share:

- [Breaking Qanat on Purpose](https://claude.ai/code/artifact/067ddaba-29b4-4356-a8be-22dda69b3c83) — the engine
- [Clicking Every Button in Qanat](https://claude.ai/code/artifact/c7018f9f-c61a-4847-b18f-0d82f7254e4a) — the console
- [Does the Backtest Tell the Truth?](https://claude.ai/code/artifact/0c725dfa-416c-4cc0-a5ae-b3e19ccaaaeb) — the workflow

## Naming a finding

Findings get a flat id that is never reused: `QA-074`, `QA-075`, and on. The first
run used per-surface prefixes (`F-`, `W-`, `U-`, `G-`, `R-`) before it was clear
this would repeat; those are kept as they are, because they are cited in published
reports, in commit messages and in 36 test names. New runs continue from **QA-074**.
