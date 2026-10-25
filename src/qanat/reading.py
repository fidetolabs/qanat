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

from typing import Any

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


__all__ = ["PAGE", "ReadingError", "check_number", "list_sections", "read_facts",
           "read_section", "search_documents"]
