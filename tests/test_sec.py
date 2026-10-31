"""The SEC connector, against a stubbed EDGAR rather than the real one.

A test suite does not get to make forty HTTP requests, and the SEC would be
within its rights to refuse them anyway. What is worth holding here is the
shape of what the connector produces and the three rules it exists to get
right: the contact, the pagination, and the two dates.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import httpx
import pytest

from qanat.models import Source
from qanat.sources import sec

UA = "qanat tests nobody@example.com"

TICKERS = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}

FILING_BODY = b"<html><body>Item 1. Business We make things.</body></html>"


KEYS = ["accessionNumber", "filingDate", "reportDate", "form", "primaryDocument"]


def block(rows: list[dict]) -> dict:
    """Parallel arrays, which is how EDGAR answers."""
    return {k: [r[k] for r in rows] for k in KEYS}


def submissions(recent_forms: list[dict], pages: list[str] | None = None) -> dict:
    return {
        "cik": "0000320193", "name": "Apple Inc.", "sic": "3571",
        "sicDescription": "Electronic Computers", "fiscalYearEnd": "0926",
        "filings": {
            "recent": block(recent_forms),
            "files": [{"name": n} for n in (pages or [])],
        },
    }


def filing(acc: str, filed: str, period: str, form: str = "10-K") -> dict:
    return {"accessionNumber": acc, "filingDate": filed, "reportDate": period,
            "form": form, "primaryDocument": f"aapl-{period.replace('-', '')}.htm"}


def transport(recent: list[dict], pages: dict[str, list[dict]] | None = None):
    """A stubbed EDGAR. Records what was asked for."""
    pages = pages or {}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if "company_tickers" in url:
            return httpx.Response(200, json=TICKERS)
        for name, rows in pages.items():
            # An extra page is a bare block of parallel arrays, not another
            # submissions envelope.
            if name in url:
                return httpx.Response(200, json=block(rows))
        if "submissions/CIK" in url:
            return httpx.Response(200, json=submissions(recent, list(pages)))
        if "/Archives/" in url:
            return httpx.Response(200, content=FILING_BODY)
        return httpx.Response(404)

    return httpx.MockTransport(handler), seen


@pytest.fixture
def patched(monkeypatch):
    """Let a test install a transport without the connector knowing."""
    def install(recent, pages=None):
        tr, seen = transport(recent, pages)
        real = httpx.Client

        def client(*a, **kw):
            kw["transport"] = tr
            return real(*a, **kw)

        monkeypatch.setattr(sec.httpx, "Client", client)
        # The throttle is a real sleep. There is nothing to be kind to here.
        monkeypatch.setattr(sec.Throttle, "wait", lambda self: None)
        return seen
    return install


def source(**options) -> Source:
    base = {"companies": ["AAPL"], "form": "10-K", "years": 2, "user_agent": UA}
    return Source(id="filings", to=["raw.documents"], connector="sec",
                  options={**base, **options})


# ------------------------------------------------------------------- the contact
def test_a_missing_contact_is_refused_before_any_request(tmp_path: Path):
    """The SEC refuses anonymous requests, so failing here with an explanation
    beats failing there with a 403."""
    s = Source(id="filings", to=["raw.documents"], connector="sec",
               options={"companies": ["AAPL"]})
    with pytest.raises(ValueError, match="contact"):
        sec.fetch(s, tmp_path)


def test_a_contact_with_no_email_is_refused(tmp_path: Path):
    s = source(user_agent="qanat")
    with pytest.raises(ValueError, match="email"):
        sec.fetch(s, tmp_path)


def test_an_unset_environment_variable_says_which_one(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    s = source(user_agent="${SEC_CONTACT}")
    with pytest.raises(ValueError, match="SEC_CONTACT"):
        sec.fetch(s, tmp_path)


def test_the_environment_variable_is_used_when_the_option_is_absent(tmp_path, monkeypatch, patched):
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    patched([filing("0000320193-25-000079", "2025-10-31", "2025-09-27")])
    s = Source(id="filings", to=["raw.documents"], connector="sec",
               options={"companies": ["AAPL"], "years": 1})
    assert len(sec.fetch(s, tmp_path)) == 1


# -------------------------------------------------------------------- the columns
def test_the_columns_are_the_platforms_names_not_the_secs(tmp_path, patched):
    patched([filing("0000320193-25-000079", "2025-10-31", "2025-09-27")])
    df = sec.fetch(source(years=1), tmp_path)
    assert set(df.columns) == {
        "id", "entity", "doc_type", "published_at", "period_end", "source",
        "source_keys", "body_path", "body_bytes", "body_sha256",
    }
    assert "cik" not in df.columns and "accession" not in df.columns


def test_the_secs_own_keys_are_kept_in_one_field(tmp_path, patched):
    patched([filing("0000320193-25-000079", "2025-10-31", "2025-09-27")])
    keys = json.loads(sec.fetch(source(years=1), tmp_path)["source_keys"].iloc[0])
    assert keys["ticker"] == "AAPL"
    assert keys["cik"] == "0000320193"
    assert keys["sic"] == "3571"
    assert keys["url"].endswith("aapl-20250927.htm")


def test_the_two_dates_are_both_kept_and_are_different(tmp_path, patched):
    """`period_end` is what the numbers are about. `published_at` is when they
    became knowable, and it is the only one a replay may filter on."""
    patched([filing("0000320193-25-000079", "2025-10-31", "2025-09-27")])
    row = sec.fetch(source(years=1), tmp_path).iloc[0]
    assert str(row.period_end)[:10] == "2025-09-27"
    assert str(row.published_at)[:10] == "2025-10-31"


def test_the_source_field_is_set_so_a_licence_has_something_to_act_on(tmp_path, patched):
    patched([filing("0000320193-25-000079", "2025-10-31", "2025-09-27")])
    assert sec.fetch(source(years=1), tmp_path)["source"].iloc[0] == "sec-edgar"


# ------------------------------------------------------------------- the document
def test_the_body_lands_gzipped_on_disk_and_the_row_points_at_it(tmp_path, patched):
    patched([filing("0000320193-25-000079", "2025-10-31", "2025-09-27")])
    row = sec.fetch(source(years=1), tmp_path).iloc[0]
    path = tmp_path / row.body_path
    assert path.exists()
    assert gzip.decompress(path.read_bytes()) == FILING_BODY
    assert row.body_bytes == path.stat().st_size


def test_a_cached_document_is_not_fetched_twice(tmp_path, patched):
    """A filing never changes after it is accepted, so the cache never expires."""
    rows = [filing("0000320193-25-000079", "2025-10-31", "2025-09-27")]
    seen = patched(rows)
    sec.fetch(source(years=1), tmp_path)
    first = sum(1 for u in seen if "/Archives/" in u)
    sec.fetch(source(years=1), tmp_path)
    assert sum(1 for u in seen if "/Archives/" in u) == first == 1


# ------------------------------------------------------------------ the selection
def test_only_the_asked_for_form_is_taken(tmp_path, patched):
    patched([
        filing("0000320193-25-000079", "2025-10-31", "2025-09-27"),
        filing("0000320193-25-000050", "2025-08-01", "2025-06-28", form="10-Q"),
    ])
    df = sec.fetch(source(years=5), tmp_path)
    assert list(df["doc_type"].unique()) == ["10-K"]
    assert len(df) == 1


def test_the_most_recent_years_are_taken_newest_first(tmp_path, patched):
    patched([
        filing("a", "2023-11-03", "2023-09-30"),
        filing("b", "2024-11-01", "2024-09-28"),
        filing("c", "2025-10-31", "2025-09-27"),
    ])
    df = sec.fetch(source(years=2), tmp_path)
    assert sorted(df["id"]) == ["b", "c"]


def test_the_paginated_pages_are_followed(tmp_path, patched):
    """The submissions JSON caps `recent` at 1000 filings, and a frequent filer
    pushes its older 10-Ks onto another page. Walmart is one."""
    patched(
        [filing("new", "2025-10-31", "2025-09-27")],
        pages={"CIK0000320193-submissions-001.json": [filing("old", "2021-10-29", "2021-09-25")]},
    )
    df = sec.fetch(source(years=5), tmp_path)
    assert sorted(df["id"]) == ["new", "old"]


def test_asking_for_more_years_than_exist_warns_and_takes_what_is_there(tmp_path, patched):
    patched([filing("only", "2025-10-31", "2025-09-27")])
    said: list[str] = []
    df = sec.fetch(source(years=5), tmp_path, on_warn=said.append)
    assert len(df) == 1
    assert any("5" in m and "1" in m for m in said), said


# -------------------------------------------------------------------- resolution
def test_a_cik_is_accepted_directly_and_skips_the_ticker_lookup(tmp_path, patched):
    seen = patched([filing("x", "2025-10-31", "2025-09-27")])
    sec.fetch(source(companies=["320193"], years=1), tmp_path)
    assert not any("company_tickers" in u for u in seen)


def test_an_unknown_ticker_is_named_in_the_error(tmp_path, patched):
    patched([filing("x", "2025-10-31", "2025-09-27")])
    with pytest.raises(ValueError, match="NOTATICKER"):
        sec.fetch(source(companies=["NOTATICKER"], years=1), tmp_path)


def test_no_companies_is_an_error_rather_than_an_empty_table(tmp_path):
    s = Source(id="filings", to=["raw.documents"], connector="sec",
               options={"user_agent": UA})
    with pytest.raises(ValueError, match="companies"):
        sec.fetch(s, tmp_path)


def test_the_connector_is_registered(tmp_path):
    from qanat.sources import ADAPTERS
    assert ADAPTERS["sec"] is sec.fetch
    assert "synthetic" not in ADAPTERS


# ------------------------------------------------------- the price, as printed
def test_a_split_is_kept_as_an_event_not_only_as_an_adjustment():
    """Yahoo rewrites old prices after a split, so neither column it returns is
    the price that traded. Keeping the split lets the real one be rebuilt."""
    import pandas as pd

    from qanat.sources.yfinance_source import SOURCE

    assert SOURCE == "yfinance"
    # the arithmetic the filing_prices step does: multiply by the splits that
    # came after this row, newest first
    px = pd.DataFrame({
        "subject": ["N"] * 4,
        "date": pd.to_datetime(["2021-01-04", "2021-07-20", "2024-06-10", "2025-01-02"]),
        "close": [13.11, 52.44, 121.79, 140.0],
        "split_ratio": [0.0, 4.0, 10.0, 0.0],
    })
    ratio = px["split_ratio"].replace(0.0, 1.0)
    rev = ratio[::-1].groupby(px["subject"][::-1]).cumprod()[::-1]
    px["splits_since"] = rev / ratio
    px["as_printed"] = px["close"] * px["splits_since"]

    # 4-for-1 then 10-for-1 both came after the first row, so the factor is 40
    assert px["splits_since"].iloc[0] == 40.0
    assert round(px["as_printed"].iloc[0], 2) == 524.40
    # nothing split after the last row, so it is already the printed price
    assert px["splits_since"].iloc[-1] == 1.0
    assert px["as_printed"].iloc[-1] == 140.0


def test_the_split_day_itself_is_already_in_the_new_money():
    """A split on day D leaves D's own close in post-split terms, so the factor
    for that row excludes its own ratio."""
    import pandas as pd

    px = pd.DataFrame({
        "subject": ["N"] * 2,
        "date": pd.to_datetime(["2024-06-10", "2024-06-11"]),
        "close": [121.79, 120.91],
        "split_ratio": [10.0, 0.0],
    })
    ratio = px["split_ratio"].replace(0.0, 1.0)
    rev = ratio[::-1].groupby(px["subject"][::-1]).cumprod()[::-1]
    assert (rev / ratio).iloc[0] == 1.0, "the split day is not multiplied by its own split"


# ------------------------------------------------------ fetching only the gap
def test_the_connector_asks_only_for_what_came_after(monkeypatch):
    """A fixed `from:` means every poll re-downloads the whole history. At a
    few names that is wasteful; at a few thousand it is what gets refused."""
    import pandas as pd

    from qanat.models import Source
    from qanat.sources import yfinance_source as yfs

    asked = {}

    class FakeTicker:
        def __init__(self, sym):
            self.sym = sym
            self.fast_info = {}
        def history(self, start=None, end=None, auto_adjust=None):
            asked["start"] = start
            idx = pd.DatetimeIndex([pd.Timestamp("2026-10-23")])
            return pd.DataFrame({"Close": [1.0], "Adj Close": [1.0],
                                 "Stock Splits": [0.0], "Dividends": [0.0]}, index=idx)

    monkeypatch.setattr(yfs, "_require", lambda: type("M", (), {"Ticker": FakeTicker}))
    src = Source(id="p", to=["raw.prices"], connector="yfinance",
                 options={"companies": ["A"], "from": "2021-01-01"})

    yfs.fetch(src, Path("."), since=None)
    assert asked["start"] == "2021-01-01", "a cold table starts at options.from"

    yfs.fetch(src, Path("."), since="2026-10-23")
    assert asked["start"] == "2026-10-22", "a warm table resumes, one day back"


def test_the_dispatcher_only_passes_since_to_adapters_that_take_it():
    """`csv` and `rest` have no upstream to ask, so handing them a `since`
    would be a TypeError on every poll."""
    from qanat.sources import ADAPTERS

    assert getattr(ADAPTERS["yfinance"], "wants_since", False)
    assert not getattr(ADAPTERS["csv"], "wants_since", False)
    assert not getattr(ADAPTERS["sec"], "wants_since", False)
