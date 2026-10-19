# The stage contract

`qanat check` enforces every one of these, and refuses to serve a project that breaks one
unless you pass `--force`.

**The contract is four properties and no names.** A stage declares what is true of it, not
which slot it fills. Qanat used to ship five fixed kinds — `raw`, `normalized`, `features`,
`weights`, `pnl` — and a project had to be shaped like the one product it was written for.
You now choose the layers and what they are called; what follows is what cannot be bent.

## 1. A landed stage is never written by a step

A `raw` stage holds data exactly as it arrived. If a row was wrong when it came in, it stays
wrong there, and the fix lives in the next stage where it can be seen. If a step may edit a
landed stage, the only record of what the source actually sent is gone — and every
point-in-time read in the system rests on that record.

## 2. A step reads only what it declared, and writes only what it declared

`ctx.read()` refuses any table missing from the step's `from`, and naming a table in SQL does
not go around it. A missing dependency becomes an error you see, instead of a wrong number
you don't. It is also what makes the lineage graph true rather than decorative.

## 3. The graph has no cycles

A step may read from an earlier stage, or from its own stage — that is a chain, and it is the
normal way derived tables are built. It may never read from a later one. A cycle has no
order to run in, and a cycle in a scheduled pipeline is a pipeline whose output depends on when
you last ran it.

## 4. Every row carries both dates

The period a number covers, and the day it became public. `time_columns` names the second
one per table, and a screen filters on it and nothing else. This is the product: without it
a screen run "as of 2022" reads numbers nobody could have had in 2022.

## 5. Every table that is read has a producer

A step that reads `normalized.facts` when nothing writes it fails at `qanat check` rather
than at the next scheduled run. A `.sql` step writes exactly one table, because one `SELECT`
produces one result.

## What `check` warns about but allows

**A table nothing reads.** Usually a typo. Often not: a pipeline that stops at clean dated
facts is complete, because a metric is a query over those facts and a screen is a call that
returns a list. Neither is a stage. `examples/sec-10k` carries two of these warnings on
purpose.

**A universe with no membership dates.** A universe csv may carry `from` and `to` columns,
and then membership is point-in-time: a symbol counts on a date only if it had joined by then
and had not yet left. Without them the list is the same on every day — today's list applied
to the past — and every screen built on it is flattered by the names that survived. That is
a warning rather than an error, because a static list is still runnable and sometimes it is
all you have. It should never be silent.

See **[words.md](words.md)** for what each word in a project file means.

## What is deliberately not in the contract

- **How many stages, or what they are called.** Two is the starter; one or seven are equally
  valid. `qanat init` writes a sensible shape, and that is a worked example, not a rule.
- **What a number means.** Qanat does not inspect column values.
- **Table or column names.** Nothing is reserved.

**This is weaker than what it replaced, and the cost is real.** The old contract could catch
a `normalized` stage placed after something computed, or a second basket stage, because it
knew what those words meant. It cannot any more. You can now build a pipeline that passes
`check` and computes nonsense. What you get back is that the pipeline is yours to shape, and
that the four properties above hold whatever shape you choose.

## What used to be here

Three rules are gone, and they went together:

- *One `weights` stage, last.* A project no longer has to end in a portfolio, or have one at
  all. Nothing in Qanat simulates holding a position.
- *One step writes one weights table.*
- *No step reads another step's weights.*

A `pnl` stage is still rejected without a `weights` stage in front of it, because there would
be nothing for it to price.
