"""The shapes the public web actually answers in.

Both sources here are real and free. They are in this battery because synthetic
fixtures never produce the things that break a connector: a CIK whose leading
zeros carry meaning, a filer whose older filings are only on a second page, a
price that was restated by a split years after it printed, a currency that is
not the one you assumed.

    uv run pytest audit/batteries -m "audit and not network"    # skip these

**A battery runs on real data, or it does not run.** The suites that ran this
folder over generated prices went on 2026-11-09, and `audit/README.md` says why:
a number the harness invented could not fail for a reason a filing would also
cause.

**Be a good guest:** one pass per endpoint, throttled, no retries.

**And a host being down must look different from this product being broken.**
The guard below is typed -- a transport error or a 5xx skips, a 4xx raises. It
used to be a substring test against the exception text, matching any of
`timeout, connect, temporarily, 503, 502`. When the `rest` connector was deleted,
`unknown connector 'rest'` matched `connect` inside `connector`, and six dead
tests reported themselves as a host not answering for as long as anybody cared
to look.
"""

import os

import httpx
import pytest

from qanat.connectors import fetch, sec
from qanat.models import IngestionJobDefinition

pytestmark = pytest.mark.network

#: EDGAR refuses a request with no contact in it, and a shared default would put
#: every Qanat behind one identity. CI has to supply its own.
AGENT = os.environ.get("SEC_USER_AGENT")
needs_agent = pytest.mark.skipif(
    not AGENT, reason="SEC_USER_AGENT is not set, so EDGAR would refuse the request"
)


def _edgar():
    """A throttled client carrying the contact EDGAR demands."""
    return (httpx.Client(headers={"User-Agent": AGENT, "Accept-Encoding": "gzip, deflate"},
                         timeout=60.0, follow_redirects=True),
            sec.Throttle())


def _tolerate(exc: Exception):
    """Skip when the host is at fault, raise when we are.

    **By type, never by the text.** A 403 is EDGAR refusing our contact string,
    which is this product being wrong and has to go red.
    """
    if isinstance(exc, httpx.TransportError):            # dns, refused, reset, timeout
        pytest.skip(f"the host is not answering right now: {exc!r}")
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500:
        pytest.skip(f"the host answered {exc.response.status_code}")
    raise exc


# --- EDGAR ---------------------------------------------------------------------

@needs_agent
def test_a_cik_keeps_the_zeros_that_are_part_of_it():
    """`0000320193` is Apple. `320193` is a number that happens to equal it.

    The same bug class as a KRX code read as an integer, which this battery used
    to check against a csv it wrote itself. EDGAR is the real one: the ten-digit
    form is what every fact row is keyed by, so a CIK that loses its zeros
    silently stops joining.
    """
    client, throttle = _edgar()
    try:
        with client:
            out = sec.resolve_ciks(["AAPL", "320193", "0000320193"], client, throttle)
    except Exception as exc:  # noqa: BLE001 -- _tolerate decides by type
        _tolerate(exc)

    assert out["AAPL"] == "0000320193", "a ticker must resolve to the padded form"
    assert out["320193"] == "0000320193", "a bare number is padded, not left short"
    assert out["0000320193"] == "0000320193", "an already-padded CIK is unchanged"
    for written, cik in out.items():
        assert len(cik) == 10, f"{written} resolved to {cik!r}, which is not ten digits"


@needs_agent
def test_the_filings_past_the_first_thousand_are_still_followed():
    """The submissions JSON caps its inline block at 1000 and pushes the rest to
    paginated pages under `filings.files`.

    Walmart is the filer the connector's own docstring names for this, because it
    files often enough that its older 10-Ks are only on those pages. A connector
    reading `recent` alone would answer with a complete-looking list missing a
    decade, and nothing in the response says so.
    """
    client, throttle = _edgar()
    try:
        with client:
            cik = sec.resolve_ciks(["WMT"], client, throttle)["WMT"]
            company, filings = sec.submissions(cik, client, throttle)
    except Exception as exc:  # noqa: BLE001 -- _tolerate decides by type
        _tolerate(exc)

    assert "WAL" in company["name"].upper(), f"WMT resolved to {company['name']!r}"
    assert len(filings) > 1000, (
        f"only {len(filings)} filings came back, which is the inline cap -- the "
        "paginated pages under filings.files are not being followed"
    )
    annual = sorted(f["filingDate"] for f in filings if f.get("form") == "10-K")
    assert annual, "Walmart has filed 10-Ks"
    assert annual[0] < "2012-01-01", (
        f"the oldest 10-K found is {annual[0]}, so only the recent block was read"
    )


@needs_agent
def test_a_filing_is_published_after_the_period_it_covers():
    """The promise this product makes, checked against the real calendar.

    A company reports weeks or months after the period closes, which is the whole
    reason a read is as-of a date rather than as-of a row. If `filingDate` were
    ever at or before `reportDate` on real data, every as-of answer built on
    these two columns would be letting the future through.
    """
    client, throttle = _edgar()
    try:
        with client:
            cik = sec.resolve_ciks(["AAPL"], client, throttle)["AAPL"]
            _company, filings = sec.submissions(cik, client, throttle)
    except Exception as exc:  # noqa: BLE001 -- _tolerate decides by type
        _tolerate(exc)

    annual = [f for f in filings
              if f.get("form") == "10-K" and f.get("reportDate") and f.get("filingDate")]
    assert len(annual) > 5, f"only {len(annual)} usable 10-Ks came back"
    for f in annual:
        assert f["filingDate"] > f["reportDate"], (
            f"10-K {f.get('accessionNumber')} reports {f['reportDate']} and is dated "
            f"{f['filingDate']} -- a filing cannot be public before its period closes"
        )


# --- Yahoo ---------------------------------------------------------------------

@pytest.fixture
def yahoo():
    """yfinance is the `prices` extra, not a hard dependency of this package."""
    return pytest.importorskip("yfinance", reason="install the `prices` extra for this")


def _bars(tmp_path, **options):
    job = IngestionJobDefinition.model_validate(
        {"id": "px", "to": ["raw.px"], "connector": "yfinance", "options": options})
    return fetch(job, tmp_path)


def test_a_split_is_recorded_beside_the_price_it_moved(tmp_path, yahoo):
    """`close` is adjusted for every later split, so it is not what printed.

    NVIDIA split 4-for-1 in 2021 and 10-for-1 in 2024, which is why the connector
    stores `split_ratio` next to the price: `as printed = close x every
    split_ratio after that date`. Without it, dividing a restated price by an EPS
    the SEC still reports as filed mixes two footings, and a value screen is
    pulled towards whatever split.

    **Asserted as arithmetic on the rows themselves, never against a dollar
    figure typed in here** -- Yahoo revises its history, and a hardcoded price
    would go red for that rather than for a real break.
    """
    df = _bars(tmp_path, companies=["NVDA"], **{"from": "2021-01-01", "to": "2024-12-31"})
    assert not df.empty, "no rows came back for NVDA"

    splits = df[df["split_ratio"] != 0].sort_values("date")
    assert len(splits) == 2, (
        f"expected the 2021 and 2024 splits, got {len(splits)}: "
        f"{list(zip(splits['date'].astype(str), splits['split_ratio']))}"
    )
    assert sorted(float(x) for x in splits["split_ratio"]) == [4.0, 10.0]
    assert str(splits["date"].iloc[0])[:4] == "2021"
    assert str(splits["date"].iloc[1])[:4] == "2024"

    # The connector's own identity, on a day that precedes both splits: the price
    # that printed is the stored close times every ratio after it, 4 x 10 = 40.
    first = df.sort_values("date").iloc[0]
    rebuilt = float(first["close"]) * 40.0
    assert rebuilt > 400, (
        f"a 2021-01 close of {first['close']} rebuilds to {rebuilt:.2f}, too small "
        "to be the price that printed -- split_ratio is not carrying the restatement"
    )


def test_the_currency_comes_from_the_feed_and_not_an_assumption(tmp_path, yahoo):
    """Samsung Electronics on the KRX, which is two real quirks in one row.

    The ticker is `005930.KS` and those zeros are part of it, not formatting. And
    the price is in won, which no amount of guessing from the symbol would tell
    you -- the connector takes `currency` from Yahoo's own metadata for exactly
    this reason. A screen that assumed dollars would compare 71,000 against 190.
    """
    df = _bars(tmp_path, companies=["005930.KS"],
               **{"from": "2024-01-02", "to": "2024-01-31"})
    assert not df.empty, "no rows came back for 005930.KS"

    assert set(df["subject"]) == {"005930.KS"}, (
        f"the ticker came back as {set(df['subject'])}, not as it was asked for"
    )
    assert set(df["currency"]) == {"KRW"}, (
        f"currency came back as {set(df['currency'])}, so it is being assumed "
        "rather than read from the feed"
    )
    assert df["close"].min() > 1000, "a won price under 1000 is a dollar price"
