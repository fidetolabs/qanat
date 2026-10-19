"""Read inline XBRL out of a filing, and turn it into rows of `facts`.

Inline XBRL is a tagging standard, not an SEC one, so this module knows nothing
about EDGAR, 10-Ks or CIKs. It takes bytes and returns facts. Which regulator
asked for the document is the source adapter's business.

    from qanat.xbrl import read_facts
    facts = read_facts(html_bytes)

One returned fact:

    concept       'us-gaap:NonoperatingIncomeExpense'
    subject       the entity the context names, usually a regulator's id
    period_start  the day the period opens. Equal to period_end for an instant
    period_end    the day it closes
    is_instant    whether this is a balance at a moment or a flow over a span
    dims          the context's dimensions as sorted JSON, '{}' when there are none
    value         a float, with scale and sign already applied
    unit          'iso4217:USD', 'xbrli:shares', 'iso4217:USD/xbrli:shares'
    decimals      what the filer claimed about precision
    fact_id       the document's own id for the fact, for linking back to a span

**`dims` is the field that cannot be recovered later.** A revenue figure with
`StatementBusinessSegmentsAxis = AutomotiveMember` and the consolidated total
are the same concept, the same period and different numbers. Drop the dimensions
at parse time and the two become indistinguishable rows, and nothing downstream
can tell which is the company's revenue. It is stored as sorted JSON so the same
context always produces the same string.

**Three things are applied here and never left to the reader.** `scale` is a
power of ten the filer factored out, so `321` with `scale="6"` is 321,000,000.
`sign="-"` negates, and it is separate from the text because the document shows
`(321)` to a human. A `format` names a transform: the text may be `1,234`, `—`
for zero, or the word `one`. Get any of the three wrong and the number is wrong
by an order of magnitude or by its sign, which is the failure no later check
catches.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from typing import NamedTuple

IX = "{http://www.xbrl.org/2013/inlineXBRL}"
XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
XSI = "{http://www.w3.org/2001/XMLSchema-instance}"

#: English number words the SEC's own transform accepts. The registry defines
#: zero through ten and nothing above it.
_WORDS = {
    "no": 0, "none": 0, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

#: Dashes a filer may use to mean zero. Several are not ASCII.
_ZERO_DASHES = {"-", "‐", "‑", "‒", "–", "—", "―"}


class Fact(NamedTuple):
    concept: str
    subject: str
    period_start: str
    period_end: str
    is_instant: bool
    dims: str
    value: float
    unit: str
    decimals: str
    fact_id: str


class Context(NamedTuple):
    subject: str
    period_start: str
    period_end: str
    is_instant: bool
    dims: str


def parse(raw: bytes) -> ET.Element:
    """Parse a filing. Strict XML first, because inline XBRL is required to be XHTML.

    Some filers still emit an HTML entity that XML does not define. Rather than
    reach for a lenient HTML parser and inherit its guesses about structure,
    the few named entities are declared and the document is parsed again.
    """
    import html.entities as he

    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        text = raw.decode("utf-8", errors="replace")
        # Declare every named entity the document uses, then parse again as
        # XML. This keeps the strict parser, which is the one that will not
        # quietly restructure a table to make it well formed.
        predefined = {"amp", "lt", "gt", "quot", "apos"}
        names = sorted(set(re.findall(r"&([A-Za-z][A-Za-z0-9]*);", text)) - predefined)
        if not names:
            raise
        decls = "".join(
            f'<!ENTITY {n} "{he.html5.get(n + ";", he.entitydefs.get(n, ""))}">'
            for n in names
        )
        head, _, rest = text.partition("<")
        return ET.fromstring(f"{head}<!DOCTYPE html [{decls}]><{rest}")


def _dims(context: ET.Element) -> str:
    """Every dimension on a context, as sorted JSON."""
    out: dict[str, str] = {}
    for m in context.iter(XBRLDI + "explicitMember"):
        if m.get("dimension"):
            out[m.get("dimension")] = (m.text or "").strip()
    for m in context.iter(XBRLDI + "typedMember"):
        if not m.get("dimension"):
            continue
        # A typed member carries its value in a child element the taxonomy names.
        child = next(iter(m), None)
        out[m.get("dimension")] = (child.text or "").strip() if child is not None else ""
    return json.dumps(out, sort_keys=True)


def read_contexts(root: ET.Element) -> dict[str, Context]:
    """Every context in the document, by id."""
    out: dict[str, Context] = {}
    for c in root.iter(XBRLI + "context"):
        cid = c.get("id")
        if not cid:
            continue
        ident = c.find(f".//{XBRLI}identifier")
        period = c.find(f"{XBRLI}period")
        if period is None:
            continue
        instant = period.find(f"{XBRLI}instant")
        if instant is not None:
            start = end = (instant.text or "").strip()
            is_instant = True
        else:
            s = period.find(f"{XBRLI}startDate")
            e = period.find(f"{XBRLI}endDate")
            if s is None or e is None:
                continue
            start, end = (s.text or "").strip(), (e.text or "").strip()
            is_instant = False
        out[cid] = Context(
            subject=(ident.text or "").strip() if ident is not None else "",
            period_start=start, period_end=end, is_instant=is_instant, dims=_dims(c),
        )
    return out


def read_units(root: ET.Element) -> dict[str, str]:
    """Every unit, by id. A ratio is written numerator/denominator."""
    out: dict[str, str] = {}
    for u in root.iter(XBRLI + "unit"):
        uid = u.get("id")
        if not uid:
            continue
        divide = u.find(f"{XBRLI}divide")
        if divide is not None:
            num = divide.find(f".//{XBRLI}unitNumerator/{XBRLI}measure")
            den = divide.find(f".//{XBRLI}unitDenominator/{XBRLI}measure")
            out[uid] = f"{_txt(num)}/{_txt(den)}"
        else:
            out[uid] = _txt(u.find(f"{XBRLI}measure"))
    return out


def _txt(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def to_number(text: str, fmt: str) -> float | None:
    """Apply the transform the filer named. None means 'not a number'.

    The format is advisory in practice -- plenty of facts carry none -- so the
    common shapes are tried whatever it says. What the format does decide are
    the cases the text alone cannot: a dash that means zero, and a number
    written as an English word.
    """
    s = (text or "").strip().replace(" ", " ").strip()
    short = fmt.split(":")[-1].lower() if fmt else ""

    if short.startswith("fixed-zero") or (s in _ZERO_DASHES):
        return 0.0
    if short.startswith("fixed-"):
        return {"fixed-zero": 0.0, "fixed-false": 0.0, "fixed-true": 1.0}.get(short)
    if "numwords" in short:
        return float(_WORDS[s.lower()]) if s.lower() in _WORDS else None
    if not s:
        return None

    # num-comma-decimal is the European convention: 1.234,56
    if "comma-decimal" in short:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    s = s.replace("$", "").replace("%", "").replace(" ", "")
    # Accounting negatives are shown in brackets. `sign` is the tagged way to say
    # it, but the text carries them too and a stray bracket must not survive.
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if not s or s in _ZERO_DASHES:
        return 0.0
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def read_facts(raw: bytes) -> list[Fact]:
    """Every numeric fact in the document, with scale and sign applied."""
    root = parse(raw)
    return list(iter_facts(root))


def iter_facts(root: ET.Element) -> Iterator[Fact]:
    contexts = read_contexts(root)
    units = read_units(root)
    for el in root.iter(IX + "nonFraction"):
        if el.get(XSI + "nil") == "true":
            # Tagged as present and empty. A row saying nothing is worse than no row.
            continue
        ctx = contexts.get(el.get("contextRef", ""))
        concept = el.get("name")
        if ctx is None or not concept:
            continue
        value = to_number("".join(el.itertext()), el.get("format", ""))
        if value is None:
            continue
        # A power of ten the filer factored out of the printed figure.
        try:
            value *= 10 ** int(el.get("scale") or 0)
        except ValueError:
            pass
        if el.get("sign") == "-":
            value = -value
        yield Fact(
            concept=concept,
            subject=ctx.subject,
            period_start=ctx.period_start,
            period_end=ctx.period_end,
            is_instant=ctx.is_instant,
            dims=ctx.dims,
            value=value,
            unit=units.get(el.get("unitRef", ""), ""),
            decimals=el.get("decimals") or "",
            fact_id=el.get("id") or "",
        )


def taxonomy(raw: bytes) -> str:
    """Which us-gaap release the document was tagged against, if it says.

    The same concept name can change meaning between releases, so a fact without
    its taxonomy version is not fully identified.

    This reads the bytes rather than the tree on purpose. The version is declared
    as `xmlns:us-gaap="http://fasb.org/us-gaap/2025"`, and a parser resolves
    namespace prefixes and then throws the declarations away, so by the time
    there is a tree the year is gone from everything except the few elements
    that happen to be in that namespace.
    """
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    found = sorted(set(re.findall(r"https?://fasb\.org/us-gaap/(\d{4})", text)))
    return f"us-gaap/{found[-1]}" if found else ""


__all__ = [
    "Context", "Fact", "iter_facts", "parse", "read_contexts", "read_facts",
    "read_units", "taxonomy", "to_number",
]
