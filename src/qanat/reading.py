"""The reading surface: find a document, open one section of it, check a number.

Five tools, and the smallest set that lets an agent do the job. Every tool
definition is sent to the model on every request, so a sixth has to earn its
place against the cost of all five.

Five rules shape them, and each one is a thing that went wrong first.

**1. `read_section` paginates and never returns a whole Item by default.**
Item 1A runs past 100 KB in a large filer. A tool that hands an agent 100 KB
has spent the context the agent needed to think with. `list_sections` reports
each length first, so the agent chooses.

**2. `search_documents` returns locations, never bodies.** Find, then read. A
search that returns text is a second `read_section` with no page limit on it.

**3. `list_sections` reports how it decided, not just what it found.** A 10-K
names every Item on its contents page and again throughout the prose, so most
matches are not the heading. A boundary tool that returns a confident wrong
offset is worse than one that says it was unsure.

**4. `check_number` returns both numbers and says nothing about which is
right.** The agent reads "lease obligations of $16.8 billion" and asks. Two
different things produce a gap: the agent misread, or the two numbers are
genuinely different things, an undiscounted commitment against a present
value. The tool cannot tell those apart and must not pretend to.

**5. Nothing here computes a metric, a rank or a verdict.** Those are a screen.
The reading surface reads.

**6. A tool asks for the rows it wants.** Every one of these used to read its
whole table and then filter in pandas, so opening one section of one filing
read every section of every filing first. At 40 filings that is 11 ms and
nobody notices; at 31,500 it is 1,101 ms against under 1 ms filtered, and the
cost is linear in a corpus about to grow 780-fold. **The fix is a `WHERE`
clause and not a different store** -- one Parquet file holds 6 row groups and
ten files hold 10, so the file layout moves this by a tenth of a millisecond.
`store.rows()` is the narrow read; `store.read()` is still there for a caller
that genuinely wants everything.
"""

from __future__ import annotations

from typing import Any, NamedTuple

import pandas as pd

from qanat.models import Project
from qanat.store import Store


class ReadingError(Exception):
    pass


#: How much text one `read_section` call returns unless asked for less. Roughly
#: a thousand tokens: enough to reason about, small enough that a wrong guess
#: about which section to open costs little.
PAGE = 4_000


def _table(project: Project, which: str) -> str:
    corpus = project.corpus
    ref = getattr(corpus, which, "") if corpus else ""
    if not ref:
        raise ReadingError(
            f"this project does not say which table holds its {which}. "
            f"Add `corpus: {{{which}: <stage.table>}}` to qanat.yaml."
        )
    return ref


def _rows(store: Store, ref: str, **kw: Any) -> pd.DataFrame:
    """Some of a table, with the filter done in the database.

    Thin on purpose: the only thing it adds to `store.rows` is the error a
    reading tool should give when the pipeline has not run, because "has not
    been written yet" is actionable and `LookupError` is not.
    """
    if not store.exists(ref):
        raise ReadingError(f"'{ref}' does not exist yet. Run the pipeline first.")
    return store.rows(ref, **kw)


# ---------------------------------------------------------------- the map
def list_sections(store: Store, project: Project, document: str) -> dict[str, Any]:
    """Which sections one document has, how long each is, and how it was found."""
    # Not `text`: this tool reports lengths and never bodies, and on a filings
    # corpus `text` is nearly every byte in the table.
    mine = _rows(
        store, _table(project, "spans"),
        columns="section, n_chars, char_start, char_end, rule",
        where="document = ?", params=[str(document)], order_by="char_start",
    )
    if mine.empty:
        raise ReadingError(f"no sections stored for document '{document}'")
    return {
        "document": document,
        "sections": [
            {
                "section": r.section,
                "chars": int(r.n_chars),
                "char_start": int(r.char_start),
                "char_end": int(r.char_end),
                # How the boundary was decided. Detection is a heuristic on
                # documents it has not seen, so this travels with the answer.
                "rule": r.rule,
                "pages": max(1, -(-int(r.n_chars) // PAGE)),
            }
            for r in mine.itertuples()
        ],
    }


# --------------------------------------------------------------- the text
def read_section(store: Store, project: Project, document: str, section: str,
                 page: int = 1, page_size: int = PAGE) -> dict[str, Any]:
    """One section, one page at a time, with the range the page came from."""
    page = max(1, int(page))
    page_size = max(200, min(int(page_size), 20_000))
    spans = _table(project, "spans")
    start = (page - 1) * page_size
    # `substr` in the database, so a 100 KB Item 1A does not cross into Python
    # to have 4 KB taken off the front of it. `n_text` is the whole length, which
    # is what the page count and the past-the-end error are computed from.
    hit = _rows(
        store, spans,
        columns="section, rule, char_start, length(text) AS n_text, "
                "substr(text, ?, ?) AS body",
        select_params=[start + 1, page_size],
        where="document = ? AND lower(section) = lower(?)",
        params=[str(document), str(section)],
        order_by="char_start", limit=1,
    )
    if hit.empty:
        # Only on the way to an error, so the second read costs nothing in the
        # ordinary case.
        have = sorted(_rows(store, spans, columns="section",
                            where="document = ?", params=[str(document)])["section"])
        raise ReadingError(
            f"document '{document}' has no section '{section}'. It has: "
            + (", ".join(have) if have else "(none stored)")
        )
    row = hit.iloc[0]
    n_text = int(row["n_text"])
    if start >= n_text and page > 1:
        raise ReadingError(
            f"{section} of {document} is {n_text:,} characters, which is "
            f"{max(1, -(-n_text // page_size))} page(s). There is no page {page}."
        )
    body = str(row["body"])
    return {
        "document": document, "section": row["section"], "rule": row["rule"],
        "page": page, "pages": max(1, -(-n_text // page_size)),
        # Offsets into the section, and into the document the section came from.
        "chars": [start, start + len(body)],
        "document_chars": [int(row["char_start"]) + start,
                           int(row["char_start"]) + start + len(body)],
        "text": body,
    }


# -------------------------------------------------------------- the search
def search_documents(store: Store, project: Project, query: str,
                     section: str | None = None, entity: str | None = None,
                     year: int | None = None, limit: int = 20) -> dict[str, Any]:
    """Where a phrase appears. Locations, never bodies."""
    if not str(query).strip():
        raise ReadingError("search_documents needs something to look for")
    spans_ref, docs_ref = _table(project, "spans"), _table(project, "documents")
    for ref in (spans_ref, docs_ref):
        if not store.exists(ref):
            raise ReadingError(f"'{ref}' does not exist yet. Run the pipeline first.")

    # This is the one tool with no document to narrow on, so the needle is what
    # gets pushed down. Two things follow from doing it here rather than in
    # pandas: a span that does not match never crosses into Python, and `text`
    # never does at all -- which matters most in the tool whose whole promise is
    # that it returns locations and not bodies.
    #
    # `hits` is counted by how much shorter the text gets with the needle
    # removed. `position` is 1-based and 0 when absent, and the WHERE has
    # already excluded absent.
    # Two stages, and the split is the whole performance of this tool.
    #
    # Stage one matches and cuts to `limit`. `contains(lower(text), ...)` rather
    # than ILIKE, which is slower here, and rather than a lowered copy per
    # expression, which is what made the first version of this three times the
    # cost of the pandas loop it replaced.
    #
    # Stage two computes `hits` and `first_at`, on the handful of rows that
    # survived rather than on the corpus. `hits` is counted by how much shorter
    # the text gets with the needle removed; `position` is 1-based.
    where = ["contains(lower(s.text), lower(?))"]
    params: list[Any] = [str(query)]
    if section:
        where.append("lower(s.section) = lower(?)")
        params.append(str(section))
    if entity:
        where.append("CAST(d.entity AS VARCHAR) = ?")
        params.append(str(entity))
    if year:
        where.append("year(CAST(d.period_end AS TIMESTAMP)) = ?")
        params.append(int(year))

    # Ordered, where the old pandas merge left it to whatever order the table
    # happened to be in -- which a compaction would have changed anyway. A tool
    # an agent pages through should answer the same way twice.
    found = store.query(
        f"""WITH hit AS (
                SELECT s.document, s.section, s.char_start, s.n_chars, s.text,
                       d.entity, d.period_end
                FROM {store.readable(spans_ref)} s
                LEFT JOIN {store.readable(docs_ref)} d ON d.id = s.document
                WHERE {' AND '.join(where)}
                ORDER BY s.document, s.char_start
                LIMIT {max(0, int(limit))})
            SELECT document, entity, section, period_end, n_chars AS section_chars,
                   (length(lower(text)) - length(replace(lower(text), lower(?), '')))
                       / length(?)                                     AS hits,
                   char_start + position(lower(?) IN lower(text)) - 1  AS first_at
            FROM hit
            ORDER BY document, char_start""",
        [*params, str(query), str(query), str(query)],
    )
    out = [
        {"document": r.document, "entity": r.entity, "section": r.section,
         "period_end": str(r.period_end)[:10],
         "hits": int(r.hits),
         # Where to read, not what it says. Call read_section next.
         "first_at": int(r.first_at),
         "section_chars": int(r.section_chars)}
        for r in found.itertuples()
    ]
    return {"query": query, "matches": out,
            "note": "locations only. Use read_section to read one."}


# --------------------------------------------------------------- the facts
def read_facts(store: Store, project: Project, document: str,
               concept: str | None = None, limit: int = 200) -> dict[str, Any]:
    """The tagged numbers in one document."""
    mine = _rows(store, _table(project, "facts"),
                 where="document = ?", params=[str(document)])
    if concept:
        mine = mine[mine["concept"].astype(str).str.contains(str(concept), case=False)]
    if mine.empty:
        raise ReadingError(
            f"no facts for document '{document}'"
            + (f" matching concept '{concept}'" if concept else ""))
    cols = [c for c in ("concept", "dims", "period_start", "period_end", "is_instant",
                        "value", "unit", "decimals", "as_of", "taxonomy")
            if c in mine.columns]
    return {
        "document": document,
        "total": len(mine),
        "returned": int(min(len(mine), limit)),
        "facts": mine[cols].head(int(limit)).to_dict("records"),
    }


# ------------------------------------------------------------- the company
#: How many distinct concept names one filing reports unless asked for fewer.
#: A large filer tags several hundred, and the list is the way in rather than
#: the answer -- `read_facts` returns the numbers.
CONCEPTS = 50


def resolve_filer(store: Store, project: Project, filer: str) -> dict[str, Any]:
    """A ticker or a CIK -> the filer this corpus knows, or an error naming why.

    **The filer is the key and the ticker is a label on it.** The facts carry a
    CIK -- `0000320193`, not `AAPL` -- and a screen returns those, so a tool
    that could not turn a name into a filer would leave a caller holding
    numbers it cannot read back to anybody. It resolves both ways for the same
    reason: a person asks about Apple.

    Resolved against this corpus and not against EDGAR, because the question is
    which filer this project holds documents for.
    """
    docs = _table(project, "documents")
    if not store.exists(docs):
        raise ReadingError(f"'{docs}' does not exist yet. Run the pipeline first.")

    asked = str(filer).strip()
    if not asked:
        raise ReadingError("read_company needs a filer: a ticker, or a CIK")

    # The ticker and the company name live inside `source_keys`, which is the
    # connector's own record of what the SEC said. A documents table built by
    # something else may not carry it, and a corpus addressable only by CIK is
    # still a corpus -- so the column is checked rather than assumed.
    named = _has(store, docs, "source_keys")
    ticker = ("max(json_extract_string(source_keys, '$.ticker'))" if named
              else "CAST(NULL AS VARCHAR)")
    name = ("max(json_extract_string(source_keys, '$.company_name'))" if named
            else "CAST(NULL AS VARCHAR)")

    # A CIK is digits, and EDGAR pads it to ten. A screen hands back the padded
    # form; a person types 320193.
    if asked.lstrip("0").isdigit():
        where, params = "CAST(entity AS VARCHAR) IN (?, ?)", [asked, f"{int(asked):010d}"]
    elif named:
        where = "upper(json_extract_string(source_keys, '$.ticker')) = upper(?)"
        params = [asked]
    else:
        raise ReadingError(
            f"'{docs}' stores no `source_keys`, so a ticker cannot be resolved here. "
            f"Give the CIK -- which is what the facts carry and what a screen returns"
        )

    found = store.query(
        f"""SELECT CAST(entity AS VARCHAR) AS cik, {ticker} AS ticker, {name} AS name,
                   count(*) AS filings
            FROM {store.readable(docs)}
            WHERE {where}
            GROUP BY 1 ORDER BY filings DESC""",
        params,
    )
    if found.empty:
        raise ReadingError(
            f"no filer '{asked}' in this project. It holds: {_filers(store, docs, named)}"
            f". Call find_filings and ingest_filings to add one"
        )
    row = found.iloc[0]
    return {"cik": str(row["cik"]),
            "ticker": None if not row["ticker"] else str(row["ticker"]),
            "name": None if not row["name"] else str(row["name"]),
            "filings": int(row["filings"]), "resolved_from": asked}


def _has(store: Store, ref: str, column: str) -> bool:
    info = store.table_info(ref)
    return bool(info) and column in {c for c, _ in info.columns}


def _filers(store: Store, docs: str, named: bool) -> str:
    """Who this corpus does hold, for an error that is worth reading."""
    pick = ("json_extract_string(source_keys, '$.ticker')" if named
            else "CAST(entity AS VARCHAR)")
    try:
        rows = store.query(
            f"SELECT DISTINCT {pick} AS who FROM {store.readable(docs)} "
            f"WHERE who IS NOT NULL ORDER BY 1 LIMIT 25")
    except Exception:  # noqa: BLE001 -- an error about an error is not worth raising
        return "(could not be listed)"
    have = [x for x in rows["who"].astype(str) if x and x != "None"]
    return ", ".join(have) or "(nothing)"


def filer_map(store: Store, project: Project) -> dict[str, str]:
    """Every name this corpus answers to -> the filer it means.

    Tickers and CIKs both, upper-cased, so one lookup places either. Built in
    one query rather than one per name, because the caller asking is screening
    a list and resolving fifty names one at a time is fifty round trips.

    Empty when the project declares no documents table, or when the one it
    declares carries no `source_keys` -- a corpus addressable only by CIK is
    still a corpus, and a CIK needs no map.
    """
    try:
        docs = _table(project, "documents")
    except ReadingError:
        return {}
    if not store.exists(docs) or not _has(store, docs, "source_keys"):
        return {}
    rows = store.query(
        f"""SELECT DISTINCT upper(json_extract_string(source_keys, '$.ticker')) AS ticker,
                   CAST(entity AS VARCHAR) AS cik
            FROM {store.readable(docs)}
            WHERE ticker IS NOT NULL""")
    return {str(r.ticker): str(r.cik) for r in rows.itertuples() if r.ticker}


def list_companies(store: Store, project: Project, query: str | None = None,
                   limit: int = 100) -> dict[str, Any]:
    """Who this project holds filings for. The index, not one company's filings.

    **Nothing answered this before.** `read_company` takes a filer and tells you
    about it; the only listing anywhere was buried in the error you got for
    naming one it did not have. So a caller building a list of companies to
    screen had no way to find out which names would land -- and a name that does
    not land is a company quietly missing from an answer.
    """
    docs = _table(project, "documents")
    if not store.exists(docs):
        raise ReadingError(f"'{docs}' does not exist yet. Run the pipeline first.")

    named = _has(store, docs, "source_keys")
    ticker = ("max(json_extract_string(source_keys, '$.ticker'))" if named
              else "CAST(NULL AS VARCHAR)")
    name = ("max(json_extract_string(source_keys, '$.company_name'))" if named
            else "CAST(NULL AS VARCHAR)")
    forms = ("string_agg(DISTINCT CAST(doc_type AS VARCHAR), ', ')"
             if _has(store, docs, "doc_type") else "CAST(NULL AS VARCHAR)")

    where, params = "", []
    if query:
        if not named:
            raise ReadingError(
                f"'{docs}' stores no `source_keys`, so there is no name to search. "
                f"Leave `query` out to list the filers it holds")
        where = ("WHERE upper(coalesce(json_extract_string(source_keys, '$.ticker'), '')) "
                 "LIKE '%' || upper(?) || '%' OR upper(coalesce("
                 "json_extract_string(source_keys, '$.company_name'), '')) "
                 "LIKE '%' || upper(?) || '%'")
        params = [str(query), str(query)]

    rows = store.query(
        f"""SELECT CAST(entity AS VARCHAR) AS cik, {ticker} AS ticker, {name} AS name,
                   count(*) AS filings, {forms} AS forms,
                   min(CAST(published_at AS VARCHAR)) AS first_filed,
                   max(CAST(published_at AS VARCHAR)) AS last_filed
            FROM {store.readable(docs)}
            {where}
            GROUP BY 1
            ORDER BY filings DESC, cik
            LIMIT {max(0, int(limit))}""",
        params,
    )
    total = store.query(
        f"SELECT count(DISTINCT CAST(entity AS VARCHAR)) AS n "
        f"FROM {store.readable(docs)}")["n"].iloc[0]

    out = [
        {"cik": str(r.cik),
         "ticker": None if not r.ticker else str(r.ticker),
         "name": None if not r.name else str(r.name),
         "filings": int(r.filings),
         "forms": None if not r.forms else sorted(str(r.forms).split(", ")),
         "first_filed": str(r.first_filed)[:10],
         "last_filed": str(r.last_filed)[:10]}
        for r in rows.itertuples()
    ]
    return {
        "companies": out,
        "returned": len(out),
        "total": int(total),
        "searchable_by_name": named,
        "next": ("read_company(filer) for one company's filings and sections. "
                 "These are the names a screen's `universe` will place"),
    }


class CompanyIndex(NamedTuple):
    """Every name this corpus answers to, and every filer it holds.

    `names` maps an upper-cased ticker **or** CIK to the filer it means.
    `filers` is every filer in the corpus.

    **`known` is the field a caller must read before deciding a name is wrong.**
    An empty index means this project declares no documents table or has not
    been run -- not that the name does not exist. Those are different answers
    and only one of them is the caller's mistake.
    """

    names: dict[str, str]
    filers: frozenset[str]
    known: bool

    def place(self, symbol: str) -> str | None:
        """The filer this name means, or None if this index does not hold it."""
        return self.names.get(str(symbol).strip().upper())


def company_index(store: Store, project: Project) -> CompanyIndex:
    """Build the index. Empty and `known=False` when there is nothing to build from."""
    empty = CompanyIndex({}, frozenset(), False)
    try:
        docs = _table(project, "documents")
    except ReadingError:
        return empty
    if not store.exists(docs):
        return empty
    named = _has(store, docs, "source_keys")
    ticker = ("upper(json_extract_string(source_keys, '$.ticker'))" if named
              else "CAST(NULL AS VARCHAR)")
    rows = store.query(
        f"""SELECT DISTINCT {ticker} AS ticker, CAST(entity AS VARCHAR) AS cik
            FROM {store.readable(docs)}""")
    names: dict[str, str] = {}
    filers: set[str] = set()
    for r in rows.itertuples():
        cik = str(r.cik)
        filers.add(cik)
        # A filer is addressable by its padded CIK, its unpadded one, and its
        # ticker, because a screen hands back the first and a person types the
        # other two.
        names[cik.upper()] = cik
        names[cik.lstrip("0").upper() or cik.upper()] = cik
        if r.ticker:
            names[str(r.ticker)] = cik
    return CompanyIndex(names, frozenset(filers), bool(filers))


def read_company(store: Store, project: Project, filer: str,
                 frm: str | None = None, to: str | None = None,
                 form: str | None = None, limit: int = 20,
                 concept: str | None = None,
                 concepts: int = CONCEPTS) -> dict[str, Any]:
    """One filer's way in: its filings, their sections, and what is tagged.

    **It returns a map and no bodies.** `read_section` still pages the text and
    `read_facts` still returns the numbers; this is the index over them, keyed
    by the thing a person names. Every other reading tool starts from a
    document, so a caller holding a company and a question had to find the
    filings first, then the sections, then the facts -- and the thing anybody
    asks about is the company.
    """
    who = resolve_filer(store, project, filer)
    docs = _table(project, "documents")

    has_form = _has(store, docs, "doc_type")
    has_period = _has(store, docs, "period_end")
    # The period a filing reports on, or the day it was filed for a form that
    # reports on an event and so has no period at all.
    when = ("coalesce(CAST(period_end AS VARCHAR), CAST(published_at AS VARCHAR))"
            if has_period else "CAST(published_at AS VARCHAR)")

    where = ["CAST(entity AS VARCHAR) = ?"]
    params: list[Any] = [who["cik"]]
    if form:
        if not has_form:
            raise ReadingError(
                f"'{docs}' stores no `doc_type`, so it cannot be narrowed to one form")
        where.append("upper(CAST(doc_type AS VARCHAR)) = upper(?)")
        params.append(str(form))
    if frm:
        where.append(f"{when} >= ?")
        params.append(str(frm))
    if to:
        where.append(f"{when} <= ?")
        params.append(str(to))

    filings = store.query(
        f"""SELECT id,
                   {'CAST(doc_type AS VARCHAR)' if has_form else 'CAST(NULL AS VARCHAR)'}
                       AS form,
                   CAST(published_at AS VARCHAR) AS filed,
                   {'CAST(period_end AS VARCHAR)' if has_period else 'CAST(NULL AS VARCHAR)'}
                       AS period_end
            FROM {store.readable(docs)}
            WHERE {' AND '.join(where)}
            ORDER BY published_at DESC
            LIMIT {max(0, int(limit))}""",
        params,
    )
    ids = [str(x) for x in filings["id"]]

    spans_by_doc = _spans_for(store, project, ids)
    facts_by_doc = _facts_for(store, project, ids, concept, int(concepts))

    out = []
    for r in filings.itertuples():
        out.append({
            "document": str(r.id),
            "form": r.form,
            "filed": str(r.filed)[:10],
            "period_end": None if not r.period_end else str(r.period_end)[:10],
            "sections": spans_by_doc.get(str(r.id), []),
            "facts": facts_by_doc.get(str(r.id), {"total": 0, "taxonomies": {},
                                                  "concepts_total": 0, "concepts": []}),
        })
    return {
        "filer": who,
        "returned": len(out),
        "filings": out,
        "next": (
            "read_section(document, section) for the text, a page at a time. "
            "read_facts(document, concept) for the numbers. check_number(document, value) "
            "to put a number you read in prose beside what was tagged"
        ),
    }


def _spans_for(store: Store, project: Project, ids: list[str]) -> dict[str, list[dict]]:
    """Each document's sections: name, length and the rule that found it.

    Never `text`. On a filings corpus that column is nearly every byte in the
    table, and this tool's whole promise is that its answer does not grow with
    the length of a filing.
    """
    if not ids:
        return {}
    try:
        spans = _table(project, "spans")
    except ReadingError:
        return {}
    if not store.exists(spans):
        return {}
    holes = ", ".join("?" * len(ids))
    rows = store.query(
        f"""SELECT document, section, n_chars, char_start, char_end, rule
            FROM {store.readable(spans)}
            WHERE document IN ({holes})
            ORDER BY document, char_start""",
        ids,
    )
    out: dict[str, list[dict]] = {}
    for r in rows.itertuples():
        out.setdefault(str(r.document), []).append({
            "section": r.section,
            "chars": int(r.n_chars),
            "char_start": int(r.char_start),
            "char_end": int(r.char_end),
            # The rule travels with the answer, because detection is a
            # heuristic on a document it has not seen before.
            "rule": r.rule,
            "pages": max(1, -(-int(r.n_chars) // PAGE)),
        })
    return out


def _facts_for(store: Store, project: Project, ids: list[str],
               concept: str | None, limit: int) -> dict[str, dict]:
    """What is tagged in each document: how much, in which taxonomies, and which
    concepts -- the names, not the numbers."""
    if not ids:
        return {}
    try:
        facts = _table(project, "facts")
    except ReadingError:
        return {}
    if not store.exists(facts):
        return {}
    holes = ", ".join("?" * len(ids))
    where = [f"document IN ({holes})"]
    params: list[Any] = list(ids)
    if concept:
        where.append("contains(lower(concept), lower(?))")
        params.append(str(concept))

    # Counted in the database, grouped by document. A concept list is the way
    # in; `read_facts` is what returns values.
    rows = store.query(
        f"""SELECT document, concept, count(*) AS n
            FROM {store.readable(facts)}
            WHERE {' AND '.join(where)}
            GROUP BY 1, 2
            ORDER BY document, n DESC, concept""",
        params,
    )
    out: dict[str, dict] = {}
    for r in rows.itertuples():
        doc = str(r.document)
        entry = out.setdefault(doc, {"total": 0, "taxonomies": {}, "concepts_total": 0,
                                     "concepts": []})
        entry["total"] += int(r.n)
        entry["concepts_total"] += 1
        taxonomy = str(r.concept).partition(":")[0]
        entry["taxonomies"][taxonomy] = entry["taxonomies"].get(taxonomy, 0) + int(r.n)
        if len(entry["concepts"]) < limit:
            entry["concepts"].append(str(r.concept))
    for entry in out.values():
        if entry["concepts_total"] > len(entry["concepts"]):
            entry["concepts_note"] = (
                f"{len(entry['concepts'])} of {entry['concepts_total']} concepts, the "
                f"most-tagged first. Pass `concept` to filter, or raise `concepts`"
            )
    return out


# -------------------------------------------------------- the honesty check
def check_number(store: Store, project: Project, document: str, value: float,
                 within: float = 0.25, limit: int = 10) -> dict[str, Any]:
    """A number read in prose, against the tagged numbers near it.

    Returns what was tagged, the difference, and where each came from. It does
    **not** say which is right. A gap has two explanations and this tool can
    tell neither from the other: the prose was misread, or the two numbers are
    genuinely different things.
    """
    mine = _rows(store, _table(project, "facts"),
                 where="document = ?", params=[str(document)])
    if mine.empty:
        raise ReadingError(f"no facts for document '{document}'")
    target = float(value)
    mine["difference"] = mine["value"].astype(float) - target
    scale = abs(target) if target else 1.0
    mine["relative"] = (mine["difference"].abs() / scale)
    near = mine[mine["relative"] <= float(within)].nsmallest(int(limit), "relative")
    cols = [c for c in ("concept", "dims", "period_start", "period_end", "value",
                        "unit", "difference", "as_of") if c in near.columns]
    return {
        "document": document,
        "looking_for": target,
        "within": within,
        "candidates": near[cols].to_dict("records"),
        "note": (
            "These are the tagged numbers closest to the one you read. A gap "
            "means one of two things and this tool cannot tell them apart: the "
            "prose was read wrong, or the two are different measures of the "
            "same subject, such as an undiscounted commitment against a "
            "present value. Read both before deciding."
        ),
    }


__all__ = [
    "CONCEPTS",
    "PAGE",
    "CompanyIndex",
    "ReadingError",
    "check_number",
    "company_index",
    "filer_map",
    "list_companies",
    "list_sections",
    "read_company",
    "read_facts",
    "read_section",
    "resolve_filer",
    "search_documents",
]
