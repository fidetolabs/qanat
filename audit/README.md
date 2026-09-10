# The chaos audit

Breaking qanat on purpose, on an interval, and keeping what that teaches.

The reason this is a folder and not a one-off document: the value of an audit is
not the findings, it is the **catalogue**. A run that has to re-check what the last
run already covered spends its time proving things that were already true. So every
run promotes what it learns —

| what a run produces | where it goes | who runs it after that |
| --- | --- | --- |
| a finding | a case in `tests/test_audit_fixes.py` | CI, on every push |
| a discriminator | a suite in `batteries/` | on demand, and before a release |
| a lesson | a line in `SCENARIOS.md` | the next person, by reading |

— and the next run starts on new ground.

## The folders

```
lab/          machinery every run reuses. Has to keep working; a run that
              repairs its own harness first is a run that finds nothing.
batteries/    standing suites, re-run every audit. Their job is to notice a
              regression in ground that was already covered.
runs/         one folder per audit: what was tried, what was found, the report.
FINDINGS.md   the ledger. Everything ever found, and where it stands.
SCENARIOS.md  what to try, and what each thing has caught.
```

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
