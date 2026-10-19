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


def _read(store: Store, ref: str) -> pd.DataFrame:
    if not store.exists(ref):
        raise ReadingError(f"'{ref}' does not exist yet. Run the pipeline first.")
    return store.read(ref)


# ---------------------------------------------------------------- the map
def list_sections(store: Store, project: Project, document: str) -> dict[str, Any]:
    """Which sections one document has, how long each is, and how it was found."""
    spans = _read(store, _table(project, "spans"))
    mine = spans[spans["document"].astype(str) == str(document)]
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
            for r in mine.sort_values("char_start").itertuples()
        ],
    }


# --------------------------------------------------------------- the text
def read_section(store: Store, project: Project, document: str, section: str,
                 page: int = 1, page_size: int = PAGE) -> dict[str, Any]:
    """One section, one page at a time, with the range the page came from."""
    page = max(1, int(page))
    page_size = max(200, min(int(page_size), 20_000))
    spans = _read(store, _table(project, "spans"))
    hit = spans[(spans["document"].astype(str) == str(document))
                & (spans["section"].astype(str).str.lower() == str(section).lower())]
    if hit.empty:
        have = sorted(spans[spans["document"].astype(str) == str(document)]["section"])
        raise ReadingError(
            f"document '{document}' has no section '{section}'. It has: "
            + (", ".join(have) if have else "(none stored)")
        )
    row = hit.iloc[0]
    text = str(row["text"])
    start = (page - 1) * page_size
    if start >= len(text) and page > 1:
        raise ReadingError(
            f"{section} of {document} is {len(text):,} characters, which is "
            f"{max(1, -(-len(text) // page_size))} page(s). There is no page {page}."
        )
    body = text[start:start + page_size]
    return {
        "document": document, "section": row["section"], "rule": row["rule"],
        "page": page, "pages": max(1, -(-len(text) // page_size)),
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
    spans = _read(store, _table(project, "spans"))
    docs = _read(store, _table(project, "documents"))
    merged = spans.merge(
        docs[["id", "entity", "published_at", "period_end"]],
        left_on="document", right_on="id", how="left", suffixes=("", "_doc"))
    if section:
        merged = merged[merged["section"].astype(str).str.lower() == section.lower()]
    if entity:
        merged = merged[merged["entity"].astype(str) == str(entity)]
    if year:
        merged = merged[pd.to_datetime(merged["period_end"]).dt.year == int(year)]

    needle = str(query).lower()
    out = []
    for r in merged.itertuples():
        text = str(r.text).lower()
        at = text.find(needle)
        if at < 0:
            continue
        out.append({
            "document": r.document, "entity": r.entity, "section": r.section,
            "period_end": str(r.period_end)[:10],
            "hits": text.count(needle),
            # Where to read, not what it says. Call read_section next.
            "first_at": int(r.char_start) + at,
            "section_chars": int(r.n_chars),
        })
        if len(out) >= int(limit):
            break
    return {"query": query, "matches": out,
            "note": "locations only. Use read_section to read one."}


# --------------------------------------------------------------- the facts
def read_facts(store: Store, project: Project, document: str,
               concept: str | None = None, limit: int = 200) -> dict[str, Any]:
    """The tagged numbers in one document."""
    facts = _read(store, _table(project, "facts"))
    mine = facts[facts["document"].astype(str) == str(document)]
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
    facts = _read(store, _table(project, "facts"))
    mine = facts[facts["document"].astype(str) == str(document)].copy()
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
