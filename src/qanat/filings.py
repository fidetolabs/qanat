"""Which filings exist, which ones can be parsed, and landing the chosen ones.

Picking the filings is one of the two things somebody using this platform does,
and it used to mean writing a source declaration: an id, a target table and a
connector, restated every time the list of companies changed. Finding is a
question about the world, not about a pipeline, so it gets its own answer here.

**Finding and fetching are different costs, so they are different calls.**
`find` hits EDGAR's submissions index, writes nothing and stores nothing. `ingest`
spends the fetch. A caller that wants to know what *could* be looked at should not
have to download anything to find out.

## The form decides what can be parsed, and EDGAR's flag does not say so

EDGAR marks 29.8% of filings as inline XBRL, and 1.2% of them carry a financial
statement. The flag means "at least one inline tag somewhere in this filing",
which for a 10-K or a 10-Q is the same sentence as "has statements" and nowhere
else. An 8-K is 99.9% flagged and holds cover-page text tags and no numbers.
`isXBRLNumeric` is worse: it reads 4.3% on 10-Ks, which cannot be true.

So **neither flag is read anywhere in this module**, and the question is answered
twice instead, by two tests that cost different amounts:

    find      a form allowlist. No fetch, so the answer is about the form
    ingest    the taxonomy of the facts actually parsed. The true test

A filing holds financial statements if it yields `us-gaap`, `ifrs-full`,
`us-gaap-ebp` or `oef` facts. **`dei` and `ecd` alone are the cover page.** That
rule was correct on every one of 84 real primary documents sampled across 26
forms; the flag was correct on two forms.

## One pile at a time

106 form types appear in a filer's index, and supporting them is four jobs rather
than one. They sort by what they would need:

    1  10-K, 10-Q                        nothing new. Facts already land
    2  20-F, 11-K, N-CSR, 485BPOS        the metric layer must name its taxonomy
    3  8-K, 40-F, DEF 14A, 424B2, 10-K/A a label and an honest refusal
    4  6-K, 4, 13F-HR, NPORT-P, S-1 ...  a different machine entirely

**Only pile 1 is ingested.** Every other form is found and labelled, never
parsed into the corpus. Pile 3 is not a later pile -- it ships with pile 1,
because a product that ingests pile 1 and crashes on everything else is not
scoped, it is broken.

**The taxonomy test is written even though only pile 1 lands**, because it is
what makes the scope visible instead of invisible. A pile 2 form reports the
taxonomy it holds and is still refused, with its pile named, so widening the
scope later is a scope change rather than a rewrite.

## The amendment is the case that bites

A `10-K/A` carries the form name of a 10-K and the flag of a 10-K. Three opened
held two to four `dei` facts -- a public float and a share count, and no
accounting. **No allowlist can tell an amendment apart without opening it**, so
the label for one is `unknown`: not a guess in either direction.
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from qanat.models import Project, Source
from qanat.sources import sec
from qanat.store import Store

#: The three answers the cheap test can give. `statements` means facts are
#: derivable, `cover_only` means the form carries no accounting, and `unknown`
#: means the form alone cannot settle it -- which is the honest answer for an
#: amendment and for a form nobody has measured.
LABELS = ("statements", "cover_only", "unknown")

#: pile -> the forms measured into it. The order of the piles is the order they
#: are built in, and a form in no pile has not been looked at.
PILES: dict[int, tuple[str, ...]] = {
    1: ("10-K", "10-Q"),
    2: ("20-F", "11-K", "N-CSR", "N-CSRS", "485BPOS"),
    3: ("8-K", "40-F", "DEF 14A", "DEFA14A", "424B2", "424B3", "424B5",
        "S-8", "S-3", "S-3ASR", "S-4", "11-K/A"),
    # Untagged HTML, or a primary document on a private XML schema that is not
    # XBRL at all. Strict parsing dies on the first kind and never reaches the
    # second. Named here so it is not re-discovered as a bug.
    # Both spellings of the Schedule 13 forms: EDGAR's index carries
    # `SCHEDULE 13G` on recent filings and `SC 13G` on older ones, and they are
    # the same form. Checked against a live submissions index on 2026-10-31.
    4: ("6-K", "4", "3", "5", "13F-HR", "13F-NT", "NPORT-P", "144", "FWP",
        "SD", "SC 13G", "SC 13D", "SCHEDULE 13G", "SCHEDULE 13D",
        "497", "497K", "425", "S-1", "POS AM"),
}

#: The one pile that is parsed into the corpus today.
INGESTED_PILE = 1

_PILE_OF = {form: p for p, forms in PILES.items() for form in forms}

#: Concept namespaces that mean a financial statement was tagged.
STATEMENT_TAXONOMIES = frozenset({"us-gaap", "ifrs-full", "us-gaap-ebp", "oef"})

#: Concept namespaces that mean the cover page and nothing else. `dei` is the
#: cover itself; `ecd` is pay-versus-performance, which is a table about
#: compensation and not about the business.
COVER_TAXONOMIES = frozenset({"dei", "ecd"})


class FilingsError(Exception):
    """Something the caller can act on: a bad selection, or no corpus to land in."""


def is_amendment(form: str) -> bool:
    """`10-K/A` and friends. The form name says 10-K and the contents do not."""
    return str(form).strip().upper().endswith("/A")


def pile(form: str) -> int | None:
    """Which pile a form is in, or None for one nobody has measured.

    **An amendment of a pile 1 form is pile 3**, because a 10-K/A is not a 10-K:
    it carries the form name and the flag of one and holds a public float and a
    share count. An amendment of anything else is in its base form's pile --
    an amended Form 144 needs the same different machine the original does.
    """
    name = str(form).strip().upper()
    if name in _PILE_OF:
        return _PILE_OF[name]
    if is_amendment(name):
        base = _PILE_OF.get(name[:-2])
        if base is not None:
            return 3 if base == INGESTED_PILE else base
    return None


def label(form: str) -> str:
    """What the form alone says about whether facts are derivable from it.

    This is the cheap test and it is allowed to be wrong in one direction only:
    it may say `unknown`, and it may not say `statements` about a document that
    holds none. `ingest` is what settles it, by reading the taxonomy.
    """
    if is_amendment(form):
        return "unknown"
    p = pile(form)
    if p in (1, 2):
        return "statements"
    if p in (3, 4):
        return "cover_only"
    return "unknown"


def taxonomies(facts: list[Any]) -> list[str]:
    """The concept namespaces present, sorted. `us-gaap:Revenues` -> `us-gaap`."""
    return sorted({str(f.concept).partition(":")[0] for f in facts if ":" in str(f.concept)})


def holds_statements(found: list[str]) -> bool:
    """The true test: did a statement taxonomy actually come out of the document?"""
    return bool(set(found) & STATEMENT_TAXONOMIES)


def why_refused(form: str, found: list[str]) -> str | None:
    """Why this document is not being landed, or None if it is being landed.

    The two reasons are different and a caller needs to tell them apart. A pile
    2 form is a scope boundary -- the reader works and the metric layer does not
    name its taxonomy yet. A document with no statement facts in it is the form
    being what it is.
    """
    p = pile(form)
    in_pile = ", ".join(PILES[INGESTED_PILE])
    if p != INGESTED_PILE:
        where = f"pile {p}" if p is not None else "no pile -- it has not been measured"
        return (
            f"{form} is {where}, and only pile {INGESTED_PILE} ({in_pile}) is ingested "
            f"today. It was found and labelled, not parsed. Nothing is broken here"
        )
    if not holds_statements(found):
        held = ", ".join(found) or "nothing"
        return (
            f"{form} parsed, and the facts in it are {held} -- no statement taxonomy "
            f"({', '.join(sorted(STATEMENT_TAXONOMIES))}), so there is nothing to measure. "
            f"A cover page is not a filing's numbers"
        )
    return None


# -------------------------------------------------------------- subscriptions
#: How often a subscription asks EDGAR, and the cron line each one becomes.
#: Two values, because the honest answers are "check every day" and "check
#: around when the next one is due". A cron expression is not an interval a
#: person has an opinion about.
INTERVALS = {
    "daily": "{minute} {hour} * * *",
    "monthly": "{minute} {hour} 1 * *",
}


def cron_for(every: str, hour: int, minute: int = 0) -> str:
    """The cron line for an interval and an hour. The hour is the user's."""
    try:
        shape = INTERVALS[str(every).strip().lower()]
    except KeyError:
        raise FilingsError(
            f"no interval called '{every}'. The two are: {', '.join(INTERVALS)}"
        ) from None
    if not 0 <= int(hour) <= 23:
        raise FilingsError(f"hour is {hour}: an hour of the day is 0 to 23, in UTC")
    if not 0 <= int(minute) <= 59:
        raise FilingsError(f"minute is {minute}: 0 to 59")
    return shape.format(hour=int(hour), minute=int(minute))


def every_of(schedule: str | None) -> str | None:
    """Which interval a cron line came from, or None if it came from elsewhere.

    A subscription this tool wrote reads back as the interval it was asked for.
    One somebody wrote by hand reads back as its own cron line and no interval,
    which is the truth rather than a guess at what they meant.
    """
    if not schedule:
        return None
    parts = schedule.split()
    # The minute and the hour have to be a time of day. Substituting the line's
    # own fields back into the shape matches `*/5 * * * *` against `daily`,
    # because the only parts that differ are the ones being substituted.
    if len(parts) != 5 or not (parts[0].isdigit() and parts[1].isdigit()):
        return None
    for name, shape in INTERVALS.items():
        if shape.format(minute=parts[0], hour=parts[1]) == schedule:
            return name
    return None


def subscription_id(form: list[str], companies: list[str]) -> str:
    """A name a person can read, for a corpus that does not have one yet."""
    slug = re.sub(r"[^a-z0-9]+", "", "".join(form).lower()) or "any"
    head = [re.sub(r"[^a-z0-9]+", "", str(c).lower()) for c in companies[:2]]
    name = "_".join(["sub", slug, *[h for h in head if h]])
    if len(companies) > 2:
        name += f"_p{len(companies) - 2}"
    return name[:60]


def subscribe(
    project: Project,
    root: Path,
    companies: list[str],
    *,
    forms: list[str] | str = "10-K",
    frm: str | None = None,
    to: str | None = None,
    every: str = "daily",
    hour: int = 6,
    years: int = 1,
) -> dict[str, Any]:
    """Point the same selection forward: when a matching filing appears, land it.

    This is a narrow writer over machinery that already exists. A source has
    carried a cron `schedule` since the scheduler did, and `list_steps` reads it
    back; what was missing was any way to set one without writing a source
    declaration, and any way to learn whether a clock is reading it.

    **There is one subscription per corpus, and that is the contract talking
    rather than a simplification.** One table has one producer -- otherwise no
    run row can say which pass landed a given document -- so the subscription
    *is* the source that feeds the documents table. Calling this again changes
    that selection instead of starting a second one, which is why `forms` is a
    list: a whole pile in one subscription, rather than one form per corpus.

    **A form outside the ingested pile is refused here rather than later.** A
    subscription that fills a table with documents nothing can parse is worse
    than no subscription, and the refusal costs one call instead of a month.
    """
    from qanat.editor import EditorError, save_source

    if not companies:
        raise FilingsError("a subscription needs at least one company, by ticker or CIK")
    many = forms if isinstance(forms, (list, tuple)) else [forms]
    wanted = [str(f).strip().upper() for f in many if str(f).strip()]
    if not wanted:
        raise FilingsError(
            f"a subscription needs a form. The ingested pile is "
            f"{', '.join(PILES[INGESTED_PILE])}"
        )
    for form in wanted:
        if pile(form) != INGESTED_PILE:
            where = f"pile {pile(form)}" if pile(form) else "no pile -- it is unmeasured"
            raise FilingsError(
                f"{form} is {where}, and a subscription lands rows. Only pile "
                f"{INGESTED_PILE} ({', '.join(PILES[INGESTED_PILE])}) can be ingested "
                f"today, so subscribing to {form} would fill a table with documents "
                f"nothing parses. find_filings reads any form without storing it"
            )
    schedule = cron_for(every, hour)
    ref = documents_table(project)

    # The producer of the documents table, if there is one. Reusing its id is
    # what makes this an edit to the selection rather than a second source
    # writing the same table, which `check` refuses outright.
    prior = next((s for s in project.sources if s.ref == ref), None)
    if prior is not None and prior.connector != "sec":
        raise FilingsError(
            f"{ref} is fed by '{prior.id}', which is a {prior.connector} source. A "
            f"subscription is an EDGAR poll, so it cannot take over that table"
        )
    name = prior.id if prior is not None else subscription_id(wanted, [str(c) for c in companies])

    options: dict[str, Any] = {
        "companies": [str(c) for c in companies],
        "form": wanted[0] if len(wanted) == 1 else wanted,
        "years": int(years),
    }
    if frm:
        options["from"] = frm
    if to:
        options["to"] = to
    for carry in ("user_agent", "cache", "rate_per_second"):
        if prior and carry in prior.options:
            options[carry] = prior.options[carry]

    try:
        warnings = save_source(project, root, {
            "id": name, "to": [ref], "connector": "sec", "mode": "append",
            "key": ["id"], "schedule": schedule, "options": options,
        })
    except EditorError as exc:
        raise FilingsError(str(exc)) from exc

    return {
        "subscribed": name,
        "table": ref,
        "replaced": prior is not None and bool(prior.schedule),
        "companies": options["companies"],
        "forms": wanted,
        "from": frm,
        "to": to,
        "every": every,
        "hour": int(hour),
        "schedule": schedule,
        "warnings": warnings,
        "note": "call list_subscriptions to see whether anything is running this",
    }


def unsubscribe(project: Project, root: Path, sid: str) -> dict[str, Any]:
    """Stop a subscription. The rows it already landed are left alone."""
    from qanat.editor import EditorError, delete_source

    try:
        warnings = delete_source(project, root, sid)
    except EditorError as exc:
        raise FilingsError(str(exc)) from exc
    return {"unsubscribed": sid, "warnings": warnings,
            "note": "the documents it landed are still in the corpus"}


def subscriptions(
    store: Store, project: Project, sched: Any = None,
) -> dict[str, Any]:
    """What is subscribed, when each is due -- and whether a clock is attached.

    **The second half is the one this exists for.** A subscription nothing runs
    is worse than no subscription, because the caller was told it would happen.

    Over stdio this server is the client's child, so a thread it started would
    die with the conversation: there is no clock here and there cannot be one.
    A process that serves over HTTP already outlives the request, and
    `--schedule` gives it a scheduler, so there a clock can live. **That is a
    fact about process lifetime, not a rule about what an agent is allowed to
    do** -- which is why the answer is reported rather than a `start_scheduler`
    tool being offered.
    """
    from datetime import datetime, timezone

    from croniter import croniter

    last = store.last_run_by_job()
    running = sched.status() if sched is not None else {}
    now = datetime.now(timezone.utc)

    rows = []
    for src in project.sources:
        if src.connector != "sec" or not src.schedule:
            continue
        opts = src.options or {}
        was = last.get(src.id)
        due = running.get(src.id, {}).get("next_at")
        if due is None:
            try:
                due = croniter(src.schedule, now).get_next(datetime).isoformat(
                    sep=" ", timespec="seconds")
            except (ValueError, KeyError):
                due = None
        rows.append({
            "id": src.id,
            "table": src.ref,
            "companies": list(opts.get("companies") or []),
            "from_universe": opts.get("from_universe"),
            "forms": sec.forms_of(src),
            "from": opts.get("from"),
            "to": opts.get("to"),
            "schedule": src.schedule,
            "every": every_of(src.schedule),
            "last_run": None if was is None else {
                "started_at": str(was.get("started_at")),
                "ended_at": None if was.get("ended_at") is None
                            else str(was.get("ended_at")),
                "status": was.get("status"),
                # `rows_out` is what the runs table calls it. Reading `rows`
                # reported None on every pass that had in fact landed rows --
                # which is the one thing this tool exists to report honestly.
                "rows": None if was.get("rows_out") is None
                        else int(was["rows_out"]),
                "error": was.get("error"),
            },
            "next_due": due,
            "running_now": bool(running.get(src.id, {}).get("running")),
        })

    attached = sched is not None
    return {
        "subscriptions": rows,
        "clock": {
            "attached": attached,
            "what_runs_them": "the scheduler in this process" if attached else None,
            "why": (
                "this process holds a scheduler, so these run on their own"
                if attached else
                "nothing in this process is reading these cron lines. Over stdio the "
                "server is the client's child, so a thread started here dies with the "
                "conversation -- a clock cannot live in it. These will run when a "
                "recorder runs: `qanat schedule`, or `qanat mcp --http --schedule`"
            ),
        },
        "note": (
            "`list_steps` reads the same cron line back, as `schedule` on the source"
        ),
    }


# ------------------------------------------------------------------ the corpus
def documents_table(project: Project) -> str:
    """Where document rows land.

    `corpus.documents` is the project saying which table holds documents, and it
    is what the reading tools already read. A project that declares a `sec`
    source and no corpus is answered by the source's own target, because that is
    unambiguous when there is one.
    """
    if project.corpus and project.corpus.documents:
        return project.corpus.documents
    secs = [s for s in project.sources if s.connector == "sec"]
    if len(secs) == 1:
        return secs[0].ref
    raise FilingsError(
        "this project does not say which table holds documents. Set `corpus.documents` "
        "in qanat.yaml, or declare one `sec` source and its target is used"
    )


def _sec_source(project: Project) -> Source:
    """A source to carry the contact and the cache. Synthesised if there is none.

    The SEC refuses a request with no contact in the User-Agent, and that refusal
    belongs before the request rather than after it. A project with a `sec`
    source already answers the question; one without it falls back to the
    environment, which is where `SEC_USER_AGENT` lives.
    """
    for s in project.sources:
        if s.connector == "sec":
            return s
    return Source(id="filings", to=["raw.documents"], connector="sec", options={})


def _stored(store: Store, ref: str, ids: list[str]) -> set[str]:
    """Which of these accession numbers the project already holds.

    Filtered in the database. Reading the id column of a corpus to check
    membership of forty accessions is the bug the reading rules exist to stop.
    """
    if not ids or not store.exists(ref):
        return set()
    try:
        holes = ", ".join("?" * len(ids))
        df = store.rows(ref, where=f"id IN ({holes})", params=ids, columns="id")
    except Exception:  # noqa: BLE001 -- a corpus we cannot read is not a corpus with rows
        return set()
    return set(df["id"].astype(str)) if len(df) else set()


def _in_window(row: dict, frm: str | None, to: str | None) -> bool:
    """Is this filing inside the period asked for?

    The period is matched on `reportDate` -- the fiscal period the document
    reports on -- and falls back to the filing date for the forms that have no
    reporting period at all. An 8-K reports on an event rather than a quarter,
    so it carries no `reportDate`, and dropping those would answer "no 8-Ks in
    2024" about a company that filed nine.
    """
    when = row.get("reportDate") or row.get("filingDate") or ""
    if frm and when < frm:
        return False
    return not (to and when > to)


# -------------------------------------------------------------------- find
def find(
    store: Store,
    project: Project,
    companies: list[str],
    *,
    form: str | None = None,
    frm: str | None = None,
    to: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """What EDGAR holds for these companies, and whether each one can be parsed.

    Writes nothing: no store write, no run row, no cached body. **An empty list
    is an answer.** A company with no filing of that form in that period has
    none, and saying so is not an error -- the caller asked a question about the
    world and got the true answer.

    `form` is optional and unrestricted, because the submissions index is uniform
    across every form a filer uses. What is not uniform is whether anything can be
    parsed out of the result, which is the `ingestable` field.
    """
    src = _sec_source(project)
    wanted = str(form).strip().upper() if form else None
    try:
        ref = documents_table(project)
    except FilingsError:
        ref = ""

    headers = {"User-Agent": sec.user_agent(src), "Accept-Encoding": "gzip, deflate"}
    throttle = sec.Throttle()
    rows: list[dict[str, Any]] = []
    resolved: dict[str, str] = {}

    with httpx.Client(headers=headers, timeout=60.0, follow_redirects=True) as client:
        ciks = sec.resolve_ciks([str(c) for c in companies], client, throttle)
        for written, cik in ciks.items():
            resolved[written] = cik
            company, filings = sec.submissions(cik, client, throttle)
            mine = [
                f for f in filings
                if (not wanted or str(f.get("form", "")).upper() == wanted)
                and _in_window(f, frm, to)
            ]
            mine.sort(key=lambda f: f.get("filingDate", ""), reverse=True)
            for f in mine[:limit]:
                this = str(f.get("form", ""))
                rows.append({
                    "company": written,
                    "company_name": company.get("name"),
                    "entity": cik,
                    "accession": f.get("accessionNumber"),
                    "form": this,
                    "filed": f.get("filingDate"),
                    "period_end": f.get("reportDate") or None,
                    "primary_document": f.get("primaryDocument"),
                    "ingestable": label(this),
                    "pile": pile(this),
                    "ingested_here": False,
                })

    have = _stored(store, ref, [r["accession"] for r in rows]) if ref else set()
    for r in rows:
        r["ingested_here"] = r["accession"] in have

    forms = sorted({r["form"] for r in rows})
    return {
        "companies": resolved,
        "form": wanted,
        "from": frm,
        "to": to,
        "found": len(rows),
        "forms": forms,
        "filings": rows,
        "ingestable_note": (
            f"`ingestable` is read off the form, not off EDGAR's inline-XBRL flag, which "
            f"is true for 29.8% of filings and means financial statements in 1.2%. "
            f"`unknown` is an amendment or an unmeasured form: only opening it settles it. "
            f"Only pile {INGESTED_PILE} ({', '.join(PILES[INGESTED_PILE])}) is ingested today"
        ),
    }


# ------------------------------------------------------------------ ingest
def ingest(
    store: Store,
    project: Project,
    root: Path,
    chosen: list[dict[str, Any]],
    *,
    cache: str | None = None,
) -> dict[str, Any]:
    """Fetch the chosen filings, parse them, and land the ones in scope.

    Each entry names a company -- a ticker or a CIK -- and an accession number.
    The submissions index is read again to get the form, the dates and the
    primary document, so a caller holding only an accession number can call this.

    **Every chosen filing is fetched, including one that will be refused.** The
    refusal reports the taxonomy the document actually holds, and that cannot be
    known without opening it. The body is cached either way, so the fetch is paid
    once and a later widening of the scope costs nothing.

    **A form with nothing to parse lands zero rows and says why.** It does not
    raise: an untagged form is filed as loose HTML, strict XML parsing dies on it,
    and `ET.ParseError` out of a tool is not an answer a caller can act on.
    """
    from qanat.xbrl import read_facts

    if not chosen:
        raise FilingsError(
            "nothing to ingest. Pass `filings`, each one {company, accession}, from "
            "find_filings"
        )
    ref = documents_table(project)
    src = _sec_source(project)
    where = root / str(cache or src.options.get("cache") or "./data/documents")

    headers = {"User-Agent": sec.user_agent(src), "Accept-Encoding": "gzip, deflate"}
    throttle = sec.Throttle()
    out: list[dict[str, Any]] = []
    landing: list[dict[str, Any]] = []

    with httpx.Client(headers=headers, timeout=60.0, follow_redirects=True) as client:
        asked = [str(c.get("company") or c.get("entity") or c.get("cik") or "") for c in chosen]
        ciks = sec.resolve_ciks([a for a in dict.fromkeys(asked) if a], client, throttle)
        # One submissions read per company, not per filing.
        index: dict[str, tuple[dict, dict[str, dict]]] = {}
        for written, cik in ciks.items():
            company, filings = sec.submissions(cik, client, throttle)
            index[written] = (company, {str(f.get("accessionNumber")): f for f in filings})

        for want in chosen:
            asked_as = str(want.get("company") or want.get("entity") or want.get("cik") or "")
            accession = str(want.get("accession") or "").strip()
            if asked_as not in index:
                out.append({"accession": accession, "ingested": False,
                            "reason": f"no company '{asked_as}' in this request's lookup"})
                continue
            company, by_accession = index[asked_as]
            row = by_accession.get(accession)
            if row is None:
                out.append({
                    "accession": accession, "company": asked_as, "ingested": False,
                    "reason": f"{asked_as} has no filing with accession {accession!r}. "
                              f"Call find_filings first and pass the rows it returns",
                })
                continue

            form = str(row.get("form", ""))
            cik = ciks[asked_as]
            found: list[str] = []
            facts = 0
            try:
                path, size, digest = sec.fetch_body(cik, row, client, throttle, where)
            except (httpx.HTTPError, OSError) as exc:
                out.append({"accession": accession, "company": asked_as, "form": form,
                            "ingested": False,
                            "reason": f"the fetch failed: {type(exc).__name__}: {exc}"})
                continue

            raw = gzip.decompress(path.read_bytes())
            try:
                parsed = read_facts(raw)
                found, facts = taxonomies(parsed), len(parsed)
            except Exception as exc:  # noqa: BLE001 -- an untagged form is an answer
                out.append({
                    "accession": accession, "company": asked_as, "form": form,
                    "ingested": False, "taxonomies": [], "facts": 0,
                    "pile": pile(form),
                    "reason": (
                        f"{form} has nothing to parse: {type(exc).__name__}. Inline XBRL is "
                        f"what forces a filing to be XHTML, so an untagged form is loose HTML "
                        f"and strict parsing stops on it. The body is cached at "
                        f"{path.relative_to(root)} either way"
                    ),
                })
                continue

            refused = why_refused(form, found)
            if refused:
                out.append({"accession": accession, "company": asked_as, "form": form,
                            "ingested": False, "taxonomies": found, "facts": facts,
                            "pile": pile(form), "reason": refused})
                continue

            landing.append({
                "id": accession,
                "entity": cik,
                "doc_type": form,
                "published_at": row.get("filingDate"),
                "period_end": row.get("reportDate") or None,
                "source": sec.SOURCE,
                "source_keys": json.dumps({
                    "ticker": asked_as.upper(),
                    "company_name": company.get("name"),
                    "cik": cik,
                    "accession": accession,
                    "sic": company.get("sic"),
                    "sic_description": company.get("sicDescription"),
                    "fiscal_year_end": company.get("fiscalYearEnd"),
                    "primary_document": row.get("primaryDocument"),
                    "url": sec.ARCHIVE.format(
                        cik=int(cik), accession=accession.replace("-", ""),
                        document=row.get("primaryDocument"),
                    ),
                }, sort_keys=True),
                "body_path": str(path.relative_to(root)),
                "body_bytes": size,
                "body_sha256": digest,
            })
            out.append({"accession": accession, "company": asked_as, "form": form,
                        "ingested": True, "taxonomies": found, "facts": facts,
                        "pile": pile(form)})

    landed = 0
    if landing:
        df = pd.DataFrame(landing)
        for col in ("published_at", "period_end"):
            df[col] = pd.to_datetime(df[col], errors="coerce")
        landed = store.write(ref, df, mode="append", key=["id"])
        store.event("ok", "ingest_filings", f"landed {landed:,} rows in {ref}")

    return {
        "table": ref,
        "landed": landed,
        "refused": sum(1 for r in out if not r["ingested"]),
        "filings": out,
        "next": (
            f"the document rows are in {ref}. Run the steps that read it to get facts "
            f"and spans" if landed else "nothing landed, so there is nothing to run"
        ),
    }


__all__ = [
    "COVER_TAXONOMIES", "INGESTED_PILE", "INTERVALS", "LABELS", "PILES",
    "STATEMENT_TAXONOMIES", "FilingsError", "cron_for", "documents_table", "every_of",
    "find", "holds_statements", "ingest", "is_amendment", "label", "pile",
    "subscribe", "subscription_id", "subscriptions", "taxonomies", "unsubscribe",
    "why_refused",
]
