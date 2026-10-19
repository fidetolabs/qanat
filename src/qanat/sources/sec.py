"""SEC EDGAR filings.

    - id: filings
      to: [raw.documents]
      connector: sec
      mode: replace
      key: [id]
      options:
        companies: [AAPL, MSFT, WMT]   # ticker or CIK; CIK is the stable one
        form: 10-K                     # one form per source
        years: 5                       # the most recent N, newest first
        user_agent: "acme research ops@acme.com"
        cache: ./data/documents        # where the gzipped bodies land

One row per document, and the document itself on disk beside the store. The row
is small and indexed; the body is 2-5 MB of HTML and belongs in a file.

**The SEC wants a real contact in the User-Agent.** Not a courtesy -- requests
without one are refused, and a shared default would put every Qanat user behind
one identity. Set `user_agent`, or `SEC_USER_AGENT` in the environment.

**Finding the document, and why the obvious rule is wrong.** The submissions API
carries `primaryDocument` per filing, which is the answer. The tempting shortcut
-- take the XBRL `instance` name and swap `_htm.xml` for `.htm` -- holds for
inline XBRL, roughly 2019 onward, and silently returns the raw XBRL instance for
anything older. Picking the largest `.htm` in the folder is worse: it works until
a filing carries a big exhibit. This connector asks the API.

**`recent` is not all of them.** The submissions JSON caps its inline block at
1000 filings, and a company that files often pushes its older 10-Ks into the
paginated pages under `filings.files`. Walmart is one. Those pages are followed.

Columns, which are the platform's names and not the SEC's:

    id            the accession number, globally unique
    entity        CIK, ten digits, zero padded -- stable across renames
    doc_type      the form, e.g. 10-K
    published_at  the filing date. **This is the as-of clock**
    period_end    the fiscal period the document reports on
    source        'sec-edgar', the field a licence or a takedown acts on
    source_keys   the SEC's own keys as JSON: ticker, sic, accession, url, ...
    body_path     the gzip, relative to the project root
    body_bytes    size of the stored gzip
    body_sha256   over the uncompressed bytes

`published_at` and `period_end` are different questions and the gap between them
is the point. Costco's 2025 fiscal year ends 31 August and the filing lands 8
October: a screen reading `period_end` would see those numbers five weeks before
anyone could.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from qanat.models import Source
from qanat.sources.rest import expand

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

#: The SEC publishes 10 requests a second as the ceiling. Eight leaves room for
#: clock skew rather than finding the limit and getting a 403 for ten minutes.
RATE_PER_SECOND = 8.0

SOURCE = "sec-edgar"


class _Throttle:
    """Space requests out. One clock per fetch, not per process."""

    def __init__(self, per_second: float = RATE_PER_SECOND) -> None:
        self._gap = 1.0 / per_second
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        sleep = self._gap - (now - self._last)
        if sleep > 0:
            time.sleep(sleep)
        self._last = time.monotonic()


def _user_agent(source: Source) -> str:
    missing: set[str] = set()
    ua = expand(source.options.get("user_agent"), missing) or os.environ.get("SEC_USER_AGENT")
    if missing:
        raise ValueError(
            f"source '{source.id}': user_agent names {', '.join(sorted(missing))}, "
            "which is not set. Export it, or write the contact into qanat.yaml."
        )
    if not ua:
        raise ValueError(
            f"source '{source.id}': the SEC refuses requests with no contact in the "
            "User-Agent. Set options.user_agent to something like "
            "'acme research ops@acme.com', or export SEC_USER_AGENT."
        )
    if "@" not in ua:
        raise ValueError(
            f"source '{source.id}': user_agent '{ua}' carries no email address. "
            "The SEC asks for one so it can contact whoever is making the requests."
        )
    return ua


def _get(client: httpx.Client, url: str, throttle: _Throttle) -> httpx.Response:
    throttle.wait()
    r = client.get(url)
    r.raise_for_status()
    return r


def _ticker_map(client: httpx.Client, throttle: _Throttle) -> dict[str, str]:
    """Ticker -> ten-digit CIK, from the SEC's own list."""
    body = _get(client, TICKERS, throttle).json()
    return {
        row["ticker"].upper(): f"{int(row['cik_str']):010d}"
        for row in body.values()
    }


def _resolve(companies: list[str], client: httpx.Client, throttle: _Throttle) -> dict[str, str]:
    """Each entry is a ticker or a CIK. Returns {as written: ten-digit CIK}."""
    out: dict[str, str] = {}
    needs_lookup = [c for c in companies if not str(c).strip().lstrip("0").isdigit()]
    table = _ticker_map(client, throttle) if needs_lookup else {}
    unknown = []
    for c in companies:
        raw = str(c).strip()
        if raw.lstrip("0").isdigit():
            out[raw] = f"{int(raw):010d}"
        elif raw.upper() in table:
            out[raw] = table[raw.upper()]
        else:
            unknown.append(raw)
    if unknown:
        raise ValueError(
            f"no CIK for {', '.join(sorted(unknown))}. Give a ticker the SEC lists, "
            "or the CIK itself."
        )
    return out


def _filings(cik: str, client: httpx.Client, throttle: _Throttle) -> tuple[dict, list[dict]]:
    """The company record, and every filing it has, newest page first."""
    doc = _get(client, SUBMISSIONS.format(cik=cik), throttle).json()
    blocks = [doc["filings"]["recent"]]
    # Past 1000 filings the rest move to paginated pages. A frequent filer's older
    # 10-Ks are only there.
    for extra in doc["filings"].get("files", []):
        blocks.append(
            _get(client, f"https://data.sec.gov/submissions/{extra['name']}", throttle).json()
        )
    rows = []
    for block in blocks:
        forms = block.get("form", [])
        for i in range(len(forms)):
            rows.append({k: block[k][i] for k in block if isinstance(block[k], list)})
    return doc, rows


def _body(
    cik: str, row: dict, client: httpx.Client, throttle: _Throttle, cache: Path,
) -> tuple[Path, int, str]:
    """Fetch one document, gzip it to the cache, and report what landed.

    Already-cached documents are not re-fetched. A filing never changes after it
    is accepted -- an amendment is a new accession -- so the cache needs no
    expiry.
    """
    accession = row["accessionNumber"].replace("-", "")
    url = ARCHIVE.format(cik=int(cik), accession=accession, document=row["primaryDocument"])
    target = cache / f"{row['accessionNumber']}.htm.gz"
    if target.exists():
        raw = gzip.decompress(target.read_bytes())
        return target, target.stat().st_size, hashlib.sha256(raw).hexdigest()
    raw = _get(client, url, throttle).content
    target.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 so the same document gzips to the same bytes on every machine.
    with gzip.GzipFile(filename="", mode="wb", fileobj=target.open("wb"), mtime=0) as fh:
        fh.write(raw)
    return target, target.stat().st_size, hashlib.sha256(raw).hexdigest()


def fetch(source: Source, root: Path, on_warn=None) -> pd.DataFrame:
    opts: dict[str, Any] = source.options or {}
    companies = opts.get("companies") or []
    if not companies:
        raise ValueError(f"source '{source.id}': sec needs options.companies")
    form = str(opts.get("form", "10-K"))
    years = int(opts.get("years", 5))
    cache = root / str(opts.get("cache", "./data/documents"))

    headers = {"User-Agent": _user_agent(source), "Accept-Encoding": "gzip, deflate"}
    throttle = _Throttle(float(opts.get("rate_per_second", RATE_PER_SECOND)))
    rows: list[dict] = []

    with httpx.Client(headers=headers, timeout=60.0, follow_redirects=True) as client:
        wanted = _resolve(list(companies), client, throttle)
        for written, cik in wanted.items():
            company, filings = _filings(cik, client, throttle)
            mine = [f for f in filings if f.get("form") == form]
            mine.sort(key=lambda f: f.get("filingDate", ""), reverse=True)
            if not mine and on_warn:
                on_warn(f"{written}: no {form} on file at all")
            if len(mine) < years and on_warn:
                on_warn(
                    f"{written}: asked for {years} {form} filings and the SEC lists "
                    f"{len(mine)}. Taking what is there."
                )
            for f in mine[:years]:
                path, size, digest = _body(cik, f, client, throttle, cache)
                rows.append({
                    "id": f["accessionNumber"],
                    "entity": cik,
                    "doc_type": form,
                    # The day the numbers became public, and the only date a
                    # point-in-time read may filter on.
                    "published_at": f["filingDate"],
                    "period_end": f.get("reportDate") or None,
                    "source": SOURCE,
                    "source_keys": json.dumps({
                        "ticker": written.upper(),
                        "company_name": company.get("name"),
                        "cik": cik,
                        "accession": f["accessionNumber"],
                        "sic": company.get("sic"),
                        "sic_description": company.get("sicDescription"),
                        "fiscal_year_end": company.get("fiscalYearEnd"),
                        "primary_document": f["primaryDocument"],
                        "url": ARCHIVE.format(
                            cik=int(cik),
                            accession=f["accessionNumber"].replace("-", ""),
                            document=f["primaryDocument"],
                        ),
                    }, sort_keys=True),
                    "body_path": str(path.relative_to(root)),
                    "body_bytes": size,
                    "body_sha256": digest,
                })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in ("published_at", "period_end"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df.sort_values(["entity", "published_at"]).reset_index(drop=True)
