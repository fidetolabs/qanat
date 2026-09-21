"""The stylesheet, held to the one thing CSS will not tell you about itself.

A selector list that ends in a comma and never opens a block is legal. The parser
keeps reading selectors until it finds a `{`, so a rule that lost its declarations
does not error -- it annexes the selectors of the next rule it meets and takes that
rule's declarations instead. Two rules end up wrong and nothing anywhere says so.

That is how `body.page-backtest .bt-bar-h .chev, .btwrap .grip,` came to be styled
by the run log eleven lines below it: the chevron got `flex: 0 0 132px` inside a bar
26px tall, which stacked the Results header onto three lines and spilled it over the
strategies above, and the grip was un-hidden across a page with nothing to drag.

Comments and blank lines above a rule are this file's house style. One *inside* a
selector list is the tell, because a list written on purpose does not straddle a
section header.
"""

from __future__ import annotations

import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "src" / "qanat" / "console" / "theme.css"

#: Everything up to the first selector: whitespace, and the comment that documents
#: the rule. Dropping it is what separates house style from the bug.
LEAD = re.compile(r"^(?:\s|/\*.*?\*/)*", re.DOTALL)


def _blank_comments(src: str) -> str:
    """The source with comment bodies blanked, newlines kept so lines still line up."""
    out = list(src)
    for m in re.finditer(r"/\*.*?\*/", src, re.DOTALL):
        for i in range(m.start(), m.end()):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


def _rules(src: str):
    """(line, selector list) for each top-level rule. `@media` opens a block of
    its own, so its contents are walked as rules and the at-rule itself is not."""
    masked, depth, start = _blank_comments(src), 0, 0
    for i, ch in enumerate(masked):
        if ch == "{":
            if depth == 0:
                raw = src[start:i]
                lead = LEAD.match(raw).end()
                if not masked[start + lead : i].strip().startswith("@"):
                    yield (src.count("\n", 0, start + lead) + 1,
                           raw[lead:], masked[start + lead : i])
            depth += 1
            start = i + 1
        elif ch == "}":
            depth -= 1
            start = i + 1


def test_no_selector_list_swallowed_the_rule_below_it():
    src = CSS.read_text()
    bad = []
    for line, raw, masked in _rules(src):
        why = []
        if "/*" in raw:
            why.append("a comment sits inside it")
        if re.search(r"\n[ \t]*\n", masked.strip()):
            why.append("a blank line sits inside it")
        if masked.strip().count("\n") + 1 > 8:
            why.append("it runs over eight lines")
        if why:
            bad.append(f"  line {line}: {', '.join(why)}\n    "
                       f"{' '.join(masked.split())[:160]}")
    assert not bad, (
        "a selector list ran on past where its declarations should have been:\n"
        + "\n".join(bad)
    )


def test_a_trailing_comma_never_meets_the_block():
    """`a, b, { … }` -- the same mistake, caught before it reaches a browser."""
    masked = _blank_comments(CSS.read_text())
    hits = [masked.count("\n", 0, m.start()) + 1 for m in re.finditer(r",\s*\{", masked)]
    assert not hits, f"a selector list ends in a comma at line(s) {hits}"


def test_the_braces_balance():
    masked = _blank_comments(CSS.read_text())
    assert masked.count("{") == masked.count("}"), (
        f"{masked.count('{')} opening braces against {masked.count('}')} closing"
    )
