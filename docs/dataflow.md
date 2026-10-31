# The data flow, end to end

What happens between an HTTP request at the SEC and a tool call an agent makes. Six stations, in
order, plus one thing that is not a station because it runs underneath all of them.

Every count below was read out of [`examples/sec-10k`](../examples/sec-10k), not estimated. The
commands that reproduce them are at the bottom. The `lineage` tool answers out of the same graph, from whatever
project you are standing in.

| Station | Does | Rows out | Owned by |
| --- | --- | --- | --- |
| 0 · choose | ask EDGAR what exists, label what can be parsed | — | `filings.py` |
| 1 · ingest | fetch the primary document for the ones chosen | — | `sources/sec.py` |
| 2 · land | one small row per filing, the body gzipped beside it | 40 | `raw.documents` |
| 3 · normalize | three steps read the same body three ways | 44,048 | `normalized.*` |
| 4 · metric | SQL over the facts, with the date substituted in | 0 | `qanat.yaml` |
| 5 · serve | 33 tools over MCP, in four nested scopes | 0 | `mcp.py` |

**Stations 4 and 5 write nothing, and the zeroes are the design.** A metric is a query and a screen
is a call that returns a list, so neither owns a table. The pipeline stops at clean dated facts.

## 0 · Choose

Two calls, because finding and fetching cost different amounts. `find_filings` hits the submissions
index, writes nothing and fetches no document — it is the one that answers "what could I look at".
`ingest_filings` spends the fetch on the rows you pick.

**The form decides what can be parsed, and EDGAR's own flag does not say so.** `isInlineXBRL` is
true for 29.8% of filings in a 48,807-filing sample and means a financial statement in 1.2% of
those; for a 10-K or a 10-Q the flag and the fact are the same sentence, and nowhere else.
`isXBRLNumeric` reads 4.3% on 10-Ks, which cannot be true. **Neither is read anywhere in the
module.** The question is answered twice instead: a form allowlist when finding, and the taxonomy of
the facts actually parsed when ingesting. A filing holds statements if it yields `us-gaap`,
`ifrs-full`, `us-gaap-ebp` or `oef` facts; `dei` and `ecd` alone are the cover page.

**106 form types appear in a filer's index, and only one pile is parsed.** One live call for AAPL
since 2024 returned 200 filings in 20 distinct forms, each labelled:

| Label | Pile | Forms in that one call |
| --- | --- | --- |
| `statements` | 1 | 10-K, 10-Q — **the only pile ingested** |
| `cover_only` | 3 | 8-K, DEF 14A, 424B2, S-8, S-3ASR |
| `cover_only` | 4 | 4, 144, 3, 5, FWP, SD — a different machine entirely |
| `unknown` | — | amendments, and forms nobody has measured |

Every other form is **found and labelled, never parsed**, and refused with its pile named. Toyota's
20-F reports `ifrs-full` with 2,688 facts and is still refused: a product that ingests two forms and
crashes on the rest is not scoped, it is broken. A 6-K, SD or FWP lands zero rows and says why
rather than raising — inline XBRL is what forces a filing to be XHTML, so an untagged form is loose
HTML and strict parsing stops on it.

`subscribe_filings` points the same selection forwards, and `list_subscriptions` reports **whether
anything is running it**. Over stdio nothing is: this server is the client's child, so a thread it
started would die with the conversation. That is process lifetime, not authority.

## 1 · Ingest

The connector asks the submissions API which filings exist and takes `primaryDocument` from the
answer. Two cheaper rules were tried and both are wrong: swapping `_htm.xml` for `.htm` on the XBRL
instance name holds only for inline XBRL, roughly 2019 onward, and silently returns the raw instance
for anything older; picking the largest `.htm` in the folder works until a filing carries a big
exhibit.

The inline block of that JSON caps at 1,000 filings, so a company that files often pushes its older
10-Ks into the paginated pages under `filings.files`. Walmart is one. Those pages are followed.

No API key. The SEC refuses requests with no contact in the `User-Agent`, which is why
`SEC_USER_AGENT` has no default. Bodies are cached under `data/documents`, so a second run touches
the network only to look for filings it has not seen.

## 2 · Land

One row per document, in the platform's column names rather than the SEC's. The SEC's own keys ride
along as JSON in `source_keys` — ticker, SIC, accession, URL — so a query can group on industry
without the core knowing what a SIC code is.

| Column | Holds |
| --- | --- |
| `id` | the accession number, globally unique |
| `entity` | CIK, ten digits, zero padded — stable across renames |
| `doc_type` | the form, `10-K` |
| `published_at` | the filing date. **This is the as-of clock** |
| `period_end` | the fiscal period the document reports on |
| `source` | `sec-edgar` — the field a licence or a takedown acts on |
| `source_keys` | the SEC's own keys as JSON |
| `body_path` · `body_bytes` · `body_sha256` | where the gzip is, how big, and what it hashes to uncompressed |

The row is small enough to scan and the body is 2–5 MB of HTML, so the body is a file on disk and
not a column. **A `raw` stage is never written by a step:** if a row was wrong when it arrived it
stays wrong there, and every point-in-time read in the system rests on that record.

## 3 · Normalize

Three steps, each naming what it reads and what it writes. `ctx.read()` refuses any table the step
did not declare, and naming one in SQL does not get around it.

**`parse_facts` → `normalized.facts`, 43,104 rows** across 1,440 distinct concepts and six taxonomy
years. Three things are applied here and never left to the reader: `scale`, a power of ten the filer
factored out, so `321` at `scale="6"` is 321,000,000; `sign`, because the document shows `(321)` to
a human; and `format`, because the text may be `1,234`, an em dash for zero, or the word *one*. Get
any of the three wrong and the number is wrong by an order of magnitude or by its sign, which is the
failure no later check catches. `dims` is stored as sorted JSON because it is the field that cannot
be recovered later — segment revenue and consolidated revenue are the same concept and the same
period.

**`split_sections` → `normalized.spans`, 904 rows**, each carrying the rule that found it. Three
things in a 10-K match the obvious pattern and only one is a heading: the contents page (all
twenty-three Item strings inside 1,200 characters in Apple's 2025 filing), the cross-references in
the prose (nineteen of them, more than the fourteen real headings), and the heading itself. The
title is the signal. Every span in the example resolved to the `ordered` rule.

**The vocabulary belongs to one form, and a form without one gets no spans.** `split` is told which
form it is working in and never falls through to the 10-K's, which is how an 8-K used to come back
holding Item 1, Item 7 and Item 8 — those are `7.01` and `8.01`, a different numbering scheme read
through a pattern for a different form.

A 10-Q numbers its Items **inside Parts**, so the same number is two different sections and the Part
is part of the name: Part I Item 1 is the financial statements, Part II Item 1 is legal proceedings.
Measured on eight real 10-Qs, against the splitter as it was before:

| Form | before | after |
| --- | --- | --- |
| 10-K | 23 spans | **23, identical** — same sections, same offsets, same rules |
| 10-Q | **1 to 2 spans** | **9 to 11 spans** |

The two it had been missing are the two longest in the document — Part I Item 1 at 25,198 characters
and Part I Item 2 at 21,578. So the example's 904 is unchanged: it holds 10-Ks only, and that path
was compared document by document rather than assumed.

**`filing_prices` → `normalized.filing_prices`, 40 rows**, one per filing, on the filing's own date.
It carries `close_as_printed`, `close`, `adjusted_close`, `splits_since` and `trading_day_gap` side
by side, because the two are not interchangeable: a P/E built from a filing's EPS needs the price as
it printed, and a return needs the adjusted one. NVIDIA split twice since 2021, cumulative factor
40 — stored unadjusted, 2021-01-04 reads $13.11 against an actual $524.54.

## The clock, which is not a station

A company publishes its results weeks or months after the period they cover. Costco's 2025 fiscal
year ends 31 August and the filing lands 8 October. **So the column a screen filters on is
`published_at` and never `period_end`:** a screen reading `period_end` would see those numbers five
weeks before anyone could.

It is enforced by shadowing rather than by convention. `open_pit` builds one view per table,
filtered on that table's own time column, and puts the view schema on the search path — so a step or
a metric that names the table gets the view and cannot reach around the filter. A table with no
clock declared is the same at every date, which is right for a lookup and wrong for anything that
arrives. `time_columns` names the column per table, and the example names five.

**The dates protect a step, not you.** A step cannot see past its date; you and your agent still
can, while you are deciding what to try.

## 4 · Metric extraction

A metric is a named, versioned SQL formula in `qanat.yaml` with `:as_of` in it, written by
`define_metric` and run against the project before it is stored — so a syntax error or an unknown
column comes back as the database's own message rather than as a definition that fails on somebody
else's screen. A changed formula is a **new version beside the old one**, because an answer somebody
kept points at a version; bumping is refused rather than guessed.

The formula is shared, because `price / diluted EPS` is arithmetic with no judgement in it. **The
threshold next to it is an opinion and never lives here**, and nor does the filter it sits in. A
filter is one caller's research criteria rather than a financial dataset, and a server that cannot
tell two callers apart has nowhere to keep one correctly. No metrics table and no results layer,
for the same reason.

Two things in the two bundled metrics were wrong the first time, and both are the shape of the work
rather than a detail of it.

**A 10-K reports three years, all stamped with the same filing date.** So ordering by `as_of` alone
picks an arbitrary one of the three. The tiebreak on `period_end` is what makes it the year actually
being reported.

**Filers do not agree on a revenue tag.** NVIDIA uses `Revenues`; Apple and most others use
`RevenueFromContractWithCustomerExcludingAssessedTax`. Taking one and not the other silently dropped
NVIDIA's revenue and produced a 271% net margin. Mapping plain names onto the tags filers really use
is the large piece of work, and `pe` and `net_margin` are a worked example of it rather than a
finished map.

**The line is shared against per-user.** This end owns the data, the definitions and the execution.
The caller owns the filter, the history and the results. That only works if a screen hands back
enough to keep a record of, so it returns: the list, **the metric values the verdict was reached
on** — for the companies that failed as well as the ones that passed — each company's own forward
return, the count of how many could be evaluated, and an `execution` block naming the definition
versions it actually read and the corpus range it ran over.

`execution.id` is a **digest of those inputs, not a random handle.** An as-of date protects a replay
from a filer restating their numbers; it does not protect it from a parser being fixed and the
corpus reloaded. Same inputs, same id — so an id that moved means something underneath did, and a
record that cannot tell those apart is not a record. There is no wall clock in it: the same screen
twice is the same answer, and when a caller asked is the caller's own fact to record.

**The count is there because a list is a claim.** `coverage` is the same instinct one step earlier: a
read as of a date sees the rows published on or before it, so before the oldest row there is nothing
to read, and a screen there returns a list of nobody — which is not the same answer as nobody
passing. A window starting before the corpus is refused with the range it does hold.

`universe` bounds who is considered, as a list built from `list_companies`. It widens the answer as
well as narrowing it: a name with no value comes back unjudgeable rather than absent, and a name this
project does not hold at all is reported rather than dropped.

## 5 · Serve

| Scope | Tools | Adds |
| --- | --- | --- |
| `data` | 14 | reads the tables as they stood on a past date, lists the companies and reads one, reads the filings |
| `research` | 20 | `list_steps`, `read_step`, `screen`, `coverage`, `find_filings`, `list_subscriptions` |
| **`full`** *(default)* | 28 | `check`, `plan`, `run`, `list_runs`, `ingest_filings`, `subscribe_filings`, `unsubscribe_filings`, `save_universe` |
| `authoring` | 33 | `define_metric`, `remove_metric`, `save_step`, `remove_step`, `save_source` |

**The nesting is the information.** A server started at `research` offers the `data` tools too, and
nothing in a request can widen the scope the flag set at startup. The list is short because every
tool definition is sent on every request, so a long list costs tokens on every call and makes the
agent worse at choosing.

**The default is `full`, which is not the widest one, and that is the point of the fourth rung.**
The pipeline from a filing to a metric is the same in every project, so nobody screening filings
needs the tools that rewire it — and carrying them means an agent can rewire it by accident.
Defining a metric sits up there for a different reason: a formula is the same number for every
reader of a given deployment, so there is no private definition and the owner of one is whoever runs
it.

`lineage` and `stale_tables` stay in `data` and look like they should not: both only mean something
to a caller who may change the pipeline. But each scope is a published contract, and taking a tool
out of `data` breaks what somebody built against it worse than adding one would. Moving them is a
separate decision, not a cleanup.

**The command line is four verbs**, because this is reached by an agent: `init`, `check`, `mcp`,
`schedule`. `ls`, `graph`, `plan`, `prune`, `run`, `screen` and `serve` were each a second door onto
a question a tool already answers, which is a second place the behaviour can quietly differ. Running
one prints where it went. The clock and the server are separable now — `qanat schedule` is the clock,
`qanat mcp --http` the server, and `--schedule` puts both in one process.

Two transports, one dispatcher. `qanat mcp` speaks JSON-RPC on a pipe, which is right for one person
on one machine and cannot be the hosted one — a request arriving at a server has nowhere to keep a
child process. `mcp_http` is MCP's Streamable HTTP transport over the same tools and the same scopes,
with the scope pinned at startup and a token required on a non-loopback bind. **Neither is a public
JSON data API**, and that was refused rather than deferred.

Seven of the fourteen `data` tools are the reading surface, and the rules on them are about an
agent's context rather than about security. `list_companies` is the index of who this project holds
filings for, and `read_company` is the way into one: a ticker or a CIK in, that filer's filings with
each one's sections, lengths and boundary rules out, **and no document bodies** — the response does
not grow with the length of a filing. Every other reading tool starts from a document, and the thing
anybody asks about is the company. `list_sections` reports which rule decided each boundary, not
just what it found. `read_section` paginates always, because Item 1A can pass 100 KB and a tool that
hands an agent 100 KB has spent the context it needed to think with. `search_documents` returns
locations and never bodies. `read_facts` is the tagged half of the document. `check_number` puts a
number read in prose next to the tagged numbers near it and reports the difference, and says nothing
about which is right.

**Why that last one carries weight:** on Apple's 2025 MD&A, 45 dollar figures appear in the prose and
13 have a tagged fact within half a percent. Everything else in that section is readable only by an
agent. The product has two ways of knowing a number, with different reliability, and `check_number`
is the only test a reading has.

No tool computes a metric, a rank or a verdict. Those are steps in the engine, reached through `run`
and `screen`. The reading surface reads, and your agent is the screen.

## What this does not cover

| Limit | What it means for the flow above |
| --- | --- |
| scale | the figures are 40 filings. Nothing above has been run at the five-year, 31,500-document size |
| industry grouping | a ranking is against whatever list of companies you hand it |
| prices | Yahoo through `yfinance` — not an API with a contract behind it, and its terms do not grant redistribution |
| section detection | a heuristic, and one vocabulary per form. It gets all 904 spans in the example right and it will meet documents it has not seen. Only the 10-K and the 10-Q have a vocabulary; every other form gets none, which is the honest answer rather than a confident wrong offset |
| concurrency | one process serves one project behind a single lock, so callers queue. That is in the Python, not the storage |
| the judgement | you write the formulas and pick the thresholds, and no agent this serves can place an order |

## Reproducing the numbers

```sh
cd examples/sec-10k
qanat check && qanat schedule --run-now        # ~10 minutes the first time
duckdb -c "SELECT count(*) FROM read_parquet('data/tables/normalized__facts/*.parquet')"

# the scope counts
python -c "from qanat import mcp; print([(s, len(mcp.tools_for(s))) for s in mcp.SCOPES])"

# the span counts per form, which need a 10-Q as well as a 10-K
duckdb -c "SELECT doc_type, count(*) FROM read_parquet('data/tables/normalized__spans/*.parquet') s
           JOIN read_parquet('data/tables/raw__documents/*.parquet') d ON d.id = s.document
           GROUP BY 1"

qanat mcp --scope research                     # hand it to an agent
```

See also [contract.md](contract.md) for what `check` enforces, [words.md](words.md) for what each
word means, and [agents.md](agents.md) for the surface once you are using it.
