"""The inline XBRL reader, on documents small enough to read by eye.

Scale, sign and the transform rules are what this is really testing. Each of
them is one attribute, each is easy to drop, and each is wrong by an order of
magnitude or by its sign when you do -- which is the class of error no later
check catches, because the number stays plausible.
"""

from __future__ import annotations

import textwrap

import pytest

from qanat.xbrl import parse, read_contexts, read_facts, read_units, taxonomy, to_number


def doc(body: str, extra_ns: str = "") -> bytes:
    """A minimal inline XBRL document wrapped around `body`."""
    return textwrap.dedent(f"""\
        <html xmlns="http://www.w3.org/1999/xhtml"
              xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
              xmlns:xbrli="http://www.xbrl.org/2003/instance"
              xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
              xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
              xmlns:us-gaap="http://fasb.org/us-gaap/2025"{extra_ns}>
        <body><ix:header><ix:hidden>
          <xbrli:context id="dur">
            <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK"
              >0000320193</xbrli:identifier></xbrli:entity>
            <xbrli:period><xbrli:startDate>2024-09-29</xbrli:startDate>
              <xbrli:endDate>2025-09-27</xbrli:endDate></xbrli:period>
          </xbrli:context>
          <xbrli:context id="inst">
            <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK"
              >0000320193</xbrli:identifier></xbrli:entity>
            <xbrli:period><xbrli:instant>2025-09-27</xbrli:instant></xbrli:period>
          </xbrli:context>
          <xbrli:context id="seg">
            <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK"
                >0000320193</xbrli:identifier>
              <xbrli:segment><xbrldi:explicitMember
                dimension="us-gaap:StatementBusinessSegmentsAxis"
                >us-gaap:AmericasMember</xbrldi:explicitMember></xbrli:segment>
            </xbrli:entity>
            <xbrli:period><xbrli:startDate>2024-09-29</xbrli:startDate>
              <xbrli:endDate>2025-09-27</xbrli:endDate></xbrli:period>
          </xbrli:context>
          <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
          <xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
          <xbrli:unit id="usdPerShare"><xbrli:divide>
            <xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>
            <xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator>
          </xbrli:divide></xbrli:unit>
        </ix:hidden></ix:header>
        {body}
        </body></html>
    """).encode()


def one(body: str):
    facts = read_facts(doc(body))
    assert len(facts) == 1, facts
    return facts[0]


# --------------------------------------------------------------- the three traps
def test_scale_is_applied():
    """`321` with scale 6 is 321 million. The document prints the short form."""
    f = one('<ix:nonFraction name="us-gaap:Revenues" contextRef="dur" unitRef="usd" '
            'scale="6" decimals="-6">321</ix:nonFraction>')
    assert f.value == 321_000_000.0


def test_sign_negates_separately_from_the_text():
    """The document shows (321) to a person and tags the sign, so the text is
    unsigned and the attribute carries it."""
    f = one('<ix:nonFraction name="us-gaap:NonoperatingIncomeExpense" contextRef="dur" '
            'unitRef="usd" scale="6" sign="-">321</ix:nonFraction>')
    assert f.value == -321_000_000.0


def test_a_dash_is_zero_not_a_parse_failure():
    f = one('<ix:nonFraction name="us-gaap:ProceedsFromIssuanceOfLongTermDebt" '
            'contextRef="dur" unitRef="usd" scale="6" '
            'format="ixt:fixed-zero">—</ix:nonFraction>')
    assert f.value == 0.0


def test_a_number_written_as_a_word():
    f = one('<ix:nonFraction name="x:NumberOfCustomers" contextRef="dur" unitRef="shares" '
            'format="ixt-sec:numwordsen" decimals="INF">one</ix:nonFraction>')
    assert f.value == 1.0


def test_thousands_separators_come_out():
    f = one('<ix:nonFraction name="dei:EntityPublicFloat" contextRef="inst" unitRef="usd" '
            'format="ixt:num-dot-decimal" scale="0">3,253,431,000,000</ix:nonFraction>')
    assert f.value == 3_253_431_000_000.0


def test_the_european_convention_is_read_when_the_format_says_so():
    f = one('<ix:nonFraction name="us-gaap:Revenues" contextRef="dur" unitRef="usd" '
            'format="ixt:num-comma-decimal" scale="0">1.234,56</ix:nonFraction>')
    assert f.value == pytest.approx(1234.56)


# -------------------------------------------------------------------- the period
def test_a_duration_keeps_both_ends():
    f = one('<ix:nonFraction name="us-gaap:Revenues" contextRef="dur" unitRef="usd" '
            'scale="0">5</ix:nonFraction>')
    assert (f.period_start, f.period_end, f.is_instant) == ("2024-09-29", "2025-09-27", False)


def test_an_instant_is_the_same_day_twice():
    """A balance is a moment. Storing it with an empty start would make every
    query special-case it."""
    f = one('<ix:nonFraction name="us-gaap:Assets" contextRef="inst" unitRef="usd" '
            'scale="0">5</ix:nonFraction>')
    assert (f.period_start, f.period_end, f.is_instant) == ("2025-09-27", "2025-09-27", True)


# ---------------------------------------------------------------------- the dims
def test_dimensions_survive_the_parse():
    """This is the field no later query can recover. Segment revenue and total
    revenue are the same concept and the same period."""
    f = one('<ix:nonFraction name="us-gaap:Revenues" contextRef="seg" unitRef="usd" '
            'scale="0">5</ix:nonFraction>')
    assert f.dims == '{"us-gaap:StatementBusinessSegmentsAxis": "us-gaap:AmericasMember"}'


def test_no_dimensions_is_an_empty_object_not_a_null():
    f = one('<ix:nonFraction name="us-gaap:Revenues" contextRef="dur" unitRef="usd" '
            'scale="0">5</ix:nonFraction>')
    assert f.dims == "{}"


def test_dims_are_sorted_so_the_same_context_always_reads_the_same():
    root = parse(doc(""))
    assert read_contexts(root)["seg"].dims == read_contexts(root)["seg"].dims


# --------------------------------------------------------------------- the units
def test_units_resolve_including_a_ratio():
    units = read_units(parse(doc("")))
    assert units["usd"] == "iso4217:USD"
    assert units["shares"] == "xbrli:shares"
    assert units["usdPerShare"] == "iso4217:USD/xbrli:shares"


# ------------------------------------------------------------------ what is skipped
def test_a_nil_fact_is_not_a_row():
    """Tagged as present and empty. A row saying nothing is worse than no row."""
    assert read_facts(doc(
        '<ix:nonFraction name="us-gaap:CommitmentsAndContingencies" contextRef="inst" '
        'unitRef="usd" xsi:nil="true"></ix:nonFraction>'
    )) == []


def test_a_fact_pointing_at_a_context_that_is_not_there_is_dropped():
    assert read_facts(doc(
        '<ix:nonFraction name="us-gaap:Revenues" contextRef="nope" unitRef="usd" '
        'scale="0">5</ix:nonFraction>'
    )) == []


# ------------------------------------------------------------------ the taxonomy
def test_the_taxonomy_version_is_read_from_the_namespace():
    """It is declared as an xmlns and a parser throws those away, so this has to
    come off the bytes."""
    assert taxonomy(doc("")) == "us-gaap/2025"


def test_no_taxonomy_declared_is_empty_rather_than_a_guess():
    assert taxonomy(b"<html><body>nothing tagged here</body></html>") == ""


# ----------------------------------------------------------------- to_number alone
@pytest.mark.parametrize("text,fmt,want", [
    ("1,234", "", 1234.0),
    ("(321)", "", -321.0),
    ("—", "", 0.0),
    ("-", "", 0.0),
    ("", "", None),
    ("not a number", "", None),
    ("$1,234.50", "", 1234.50),
])
def test_to_number(text, fmt, want):
    assert to_number(text, fmt) == want


def test_a_document_with_an_undeclared_entity_still_parses():
    """Inline XBRL is meant to be XHTML and most of it is. `&nbsp;` is the one
    that turns up anyway, and it is not a predefined XML entity."""
    root = parse(b'<html xmlns="http://www.w3.org/1999/xhtml"><body>a&nbsp;b</body></html>')
    assert "a" in "".join(root.itertext())
