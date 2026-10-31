"""Finding filings, and landing only the ones that can be parsed.

Against a stubbed EDGAR. What is worth holding here is that the two calls cost
different things -- `find` writes nothing and fetches nothing -- and that the
ingestable answer is read off the taxonomy of the facts rather than off EDGAR's
own flag, which is true for 29.8% of filings and means financial statements in
1.2% of those.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import httpx
import pytest

from qanat import filings
from qanat.models import Project, Source
from qanat.store import Store

UA = "qanat tests nobody@example.com"

TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 7203, "ticker": "TM", "title": "Toyota Motor Corp"},
}

KEYS = ["accessionNumber", "filingDate", "reportDate", "form", "primaryDocument"]


# --------------------------------------------------------------- the documents
def tagged(namespace: str, concept: str) -> bytes:
    """An inline XBRL document holding exactly one fact in one taxonomy.

    The taxonomy is the whole point of the fixture: `us-gaap` is a statement,
    `dei` is the cover page, `ifrs-full` is a statement a foreign issuer filed.
    """
    return textwrap.dedent(f"""\
        <html xmlns="http://www.w3.org/1999/xhtml"
              xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
              xmlns:xbrli="http://www.xbrl.org/2003/instance"
              xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
              xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
              xmlns:{namespace}="http://example.test/{namespace}/2025">
        <body><ix:header><ix:hidden>
          <xbrli:context id="inst">
            <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK"
              >0000320193</xbrli:identifier></xbrli:entity>
            <xbrli:period><xbrli:instant>2025-09-27</xbrli:instant></xbrli:period>
          </xbrli:context>
          <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
        </ix:hidden></ix:header>
        <p><ix:nonFraction name="{namespace}:{concept}" contextRef="inst"
           unitRef="usd" decimals="-6">391035</ix:nonFraction></p>
        </body></html>
    """).encode()


#: Loose HTML with an unclosed tag -- which is how an untagged form is filed,
#: because inline XBRL is what forces a document to be XHTML. Strict XML
#: parsing stops on this, and that must not reach the caller as a traceback.
UNTAGGED = b"<html><body><p>Results of Operations<br>Revenue was $391,035<p></body></html>"

#: accession -> the body EDGAR would serve for it.
BODIES: dict[str, bytes] = {
    "0000320193-25-000001": tagged("us-gaap", "Revenues"),      # 10-K
    "0000320193-25-000002": tagged("us-gaap", "Revenues"),      # 10-Q
    "0000320193-25-000003": tagged("dei", "EntityPublicFloat"),  # 10-K/A
    "0000320193-25-000004": tagged("dei", "EntityPublicFloat"),  # 8-K
    "0000320193-25-000005": UNTAGGED,                            # 6-K
    "0000320193-25-000006": UNTAGGED,                            # S-1
    "0000320193-25-000007": UNTAGGED,                            # FWP
    "0000320193-25-000008": UNTAGGED,                            # SD
    "0000320193-25-000009": UNTAGGED,                            # SC 13G
    "0000320193-25-000010": tagged("ifrs-full", "Revenue"),      # 20-F
    "0000320193-25-000011": UNTAGGED,                            # 13F-HR
    "0000320193-25-000012": tagged("dei", "EntityPublicFloat"),  # a 10-K, cover only
}

FILINGS = [
    ("0000320193-25-000001", "2025-11-01", "2025-09-27", "10-K"),
    ("0000320193-25-000002", "2025-08-01", "2025-06-28", "10-Q"),
    ("0000320193-25-000003", "2025-11-20", "2025-09-27", "10-K/A"),
    ("0000320193-25-000004", "2025-10-30", "", "8-K"),
    ("0000320193-25-000005", "2025-07-15", "2025-06-30", "6-K"),
    ("0000320193-25-000006", "2025-06-01", "", "S-1"),
    ("0000320193-25-000007", "2025-05-01", "", "FWP"),
    ("0000320193-25-000008", "2025-04-01", "2024-12-31", "SD"),
    ("0000320193-25-000009", "2025-03-01", "", "SC 13G"),
    ("0000320193-25-000010", "2025-02-01", "2024-12-31", "20-F"),
    ("0000320193-25-000011", "2025-01-15", "2024-12-31", "13F-HR"),
    ("0000320193-25-000012", "2025-12-01", "2025-09-27", "10-K"),
    ("0000320193-20-000001", "2020-11-01", "2020-09-26", "10-K"),
]


def row(acc: str, filed: str, period: str, form: str) -> dict:
    return {"accessionNumber": acc, "filingDate": filed, "reportDate": period,
            "form": form, "primaryDocument": f"d-{acc[-6:]}.htm"}


def block(rows: list[dict]) -> dict:
    """Parallel arrays, which is how EDGAR answers."""
    return {k: [r[k] for r in rows] for k in KEYS}


def submissions_body() -> dict:
    return {
        "cik": "0000320193", "name": "Apple Inc.", "sic": "3571",
        "sicDescription": "Electronic Computers", "fiscalYearEnd": "0926",
        "filings": {"recent": block([row(*f) for f in FILINGS]), "files": []},
    }


@pytest.fixture
def edgar(monkeypatch):
    """A stubbed EDGAR that records every url asked for."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if "company_tickers" in url:
            return httpx.Response(200, json=TICKERS)
        if "submissions/CIK" in url:
            return httpx.Response(200, json=submissions_body())
        if "/Archives/" in url:
            doc = url.rsplit("/", 1)[-1]          # d-NNNNNN.htm
            tail = doc[2:8]
            match = next((a for a in BODIES if a.endswith(tail)), None)
            if match:
                return httpx.Response(200, content=BODIES[match])
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real = httpx.Client

    def client(*a, **kw):
        kw["transport"] = transport
        return real(*a, **kw)

    monkeypatch.setattr(filings.httpx, "Client", client)
    monkeypatch.setattr("qanat.sources.sec.httpx.Client", client)
    monkeypatch.setattr(filings.sec.Throttle, "wait", lambda self: None)
    return seen


# ----------------------------------------------------------------- the project
def a_project(root: Path) -> tuple[Project, Store]:
    project = Project.model_validate({
        "project": "filings",
        "store": "./data/qanat.duckdb",
        "stages": [{"id": "raw", "kind": "raw"}],
        "sources": [{
            "id": "filings", "to": ["raw.documents"], "connector": "sec",
            "mode": "append", "key": ["id"],
            "options": {"companies": ["AAPL"], "user_agent": UA,
                        "cache": "./data/documents"},
        }],
        "corpus": {"documents": "raw.documents"},
    })
    (root / "data").mkdir(parents=True, exist_ok=True)
    return project, Store(project.store_url(root))


@pytest.fixture
def project(tmp_path: Path):
    p, s = a_project(tmp_path)
    yield p, s, tmp_path
    s.close()


# ------------------------------------------------------- the form, on its own
def test_the_form_decides_the_label_and_the_amendment_is_unknown():
    """`10-K/A` carries the form name of a 10-K and holds a public float.

    No allowlist can tell an amendment apart without opening it, so the honest
    answer is that the form does not say -- not a guess in either direction.
    """
    assert filings.label("10-K") == "statements"
    assert filings.label("10-Q") == "statements"
    assert filings.label("8-K") == "cover_only"
    assert filings.label("10-K/A") == "unknown"
    assert filings.label("ZZ-99") == "unknown", "an unmeasured form is not a guess"

    assert filings.pile("10-K") == 1
    assert filings.pile("20-F") == 2
    assert filings.pile("8-K") == 3
    assert filings.pile("10-K/A") == 3, "an amendment of a pile 1 form is not pile 1"
    assert filings.pile("20-F/A") == 2, "an amendment of anything else stays in its pile"
    assert filings.pile("144/A") == 4
    assert filings.pile("6-K") == 4
    assert filings.pile("ZZ-99") is None


def test_the_true_test_is_the_taxonomy_and_a_cover_page_is_not_a_filing():
    assert filings.holds_statements(["dei", "us-gaap"])
    assert filings.holds_statements(["ifrs-full"])
    assert not filings.holds_statements(["dei"])
    assert not filings.holds_statements(["dei", "ecd"])
    assert not filings.holds_statements([])


def test_no_flag_of_edgars_is_read_anywhere_behind_the_label():
    """EDGAR's own flags are not a fallback and are not consulted.

    `isInlineXBRL` is true for 29.8% of filings and means financial statements
    in 1.2% of them. `isXBRLNumeric` reads 4.3% on 10-Ks, which cannot be true.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "qanat"
    # A *read* of the flag, not a mention of it: this module's own docstring
    # explains at length why neither flag is consulted, and must stay able to.
    reads = re.compile(r"""\[\s*["']is(?:InlineXBRL|XBRLNumeric)|"""
                       r"""get\(\s*["']is(?:InlineXBRL|XBRLNumeric)|"""
                       r"""\.is(?:InlineXBRL|XBRLNumeric)\b""")
    hits = [
        f"{path.relative_to(src)}:{i}"
        for path in src.rglob("*.py")
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if reads.search(line)
    ]
    assert hits == [], f"EDGAR's flag is being read at {hits}"


# ------------------------------------------------------------------ find
def test_a_company_with_no_filing_of_that_form_gets_an_empty_list(project, edgar):
    """An empty answer is an answer. The caller asked about the world."""
    p, store, _root = project
    out = filings.find(store, p, ["AAPL"], form="N-CSR")
    assert out["found"] == 0
    assert out["filings"] == []
    assert out["companies"] == {"AAPL": "0000320193"}


def test_a_period_with_no_filing_in_it_gets_an_empty_list(project, edgar):
    p, store, _root = project
    out = filings.find(store, p, ["AAPL"], form="10-K", frm="2010-01-01", to="2012-12-31")
    assert out["found"] == 0


def test_find_writes_nothing_and_leaves_no_run_row(project, edgar):
    p, store, root = project
    before = len(store.recent_runs(100))
    filings.find(store, p, ["AAPL"])
    assert len(store.recent_runs(100)) == before, "find left a run row"
    assert not store.exists("raw.documents"), "find wrote to the corpus"
    assert not (root / "data" / "documents").exists(), "find fetched a body"
    assert not any("/Archives/" in u for u in edgar), "find fetched a document"


def test_any_official_form_is_found_and_labelled(project, edgar):
    """The submissions index is uniform across every form a filer uses."""
    p, store, _root = project
    out = filings.find(store, p, ["AAPL"])
    by_form = {r["form"]: r for r in out["filings"]}
    for form in ("10-K", "10-Q", "20-F", "8-K", "13F-HR", "10-K/A", "S-1"):
        assert form in by_form, f"{form} was not found"
    assert by_form["20-F"]["ingestable"] == "statements"
    assert by_form["20-F"]["pile"] == 2
    assert by_form["8-K"]["ingestable"] == "cover_only"
    assert by_form["10-K/A"]["ingestable"] == "unknown"
    assert by_form["13F-HR"]["pile"] == 4


def test_every_row_carries_an_ingestable_answer(project, edgar):
    p, store, _root = project
    out = filings.find(store, p, ["AAPL"])
    assert out["found"] > 0
    for r in out["filings"]:
        assert r["ingestable"] in filings.LABELS, r


def test_a_filing_the_project_already_holds_says_so(project, edgar):
    """So an agent does not pay for the same fetch twice."""
    p, store, root = project
    filings.ingest(store, p, root, [
        {"company": "AAPL", "accession": "0000320193-25-000001"}])

    out = filings.find(store, p, ["AAPL"], form="10-K")
    held = {r["accession"]: r["ingested_here"] for r in out["filings"]}
    assert held["0000320193-25-000001"] is True
    assert held["0000320193-20-000001"] is False


def test_an_eight_k_is_found_by_its_filing_date_not_its_period(project, edgar):
    """An 8-K reports on an event, so EDGAR gives it no reportDate at all.

    Filtering those out would answer "no 8-Ks in 2025" about a company that
    filed one.
    """
    p, store, _root = project
    out = filings.find(store, p, ["AAPL"], form="8-K", frm="2025-01-01", to="2025-12-31")
    assert out["found"] == 1
    assert out["filings"][0]["period_end"] is None


# ------------------------------------------------------------------ ingest
def test_only_pile_one_lands(project, edgar):
    p, store, root = project
    out = filings.ingest(store, p, root, [
        {"company": "AAPL", "accession": "0000320193-25-000001"},   # 10-K
        {"company": "AAPL", "accession": "0000320193-25-000002"},   # 10-Q
    ])
    assert out["landed"] == 2
    assert all(r["ingested"] for r in out["filings"])
    assert {r["form"] for r in out["filings"]} == {"10-K", "10-Q"}


def test_a_twenty_f_reports_ifrs_full_and_is_refused_with_its_pile_named(project, edgar):
    """Found, labelled, read -- and not ingested. Nothing here is broken."""
    p, store, root = project
    out = filings.ingest(store, p, root, [
        {"company": "AAPL", "accession": "0000320193-25-000010"}])
    one = out["filings"][0]
    assert out["landed"] == 0
    assert one["ingested"] is False
    assert one["taxonomies"] == ["ifrs-full"]
    assert one["pile"] == 2
    assert "pile 2" in one["reason"]
    assert "Nothing is broken" in one["reason"]


@pytest.mark.parametrize(("form", "accession"), [
    ("6-K", "0000320193-25-000005"),
    ("S-1", "0000320193-25-000006"),
    ("FWP", "0000320193-25-000007"),
    ("SD", "0000320193-25-000008"),
    ("SC 13G", "0000320193-25-000009"),
])
def test_a_form_with_nothing_to_parse_lands_zero_rows_and_says_why(
        project, edgar, form, accession):
    """It does not raise. An untagged form is filed as loose HTML, and strict
    XML parsing dying on it is not an answer a caller can act on."""
    p, store, root = project
    out = filings.ingest(store, p, root, [{"company": "AAPL", "accession": accession}])
    one = out["filings"][0]
    assert out["landed"] == 0
    assert one["ingested"] is False
    assert one["form"] == form
    assert one["reason"], "a refusal with no reason is a crash with manners"
    assert "nothing to parse" in one["reason"] or "pile" in one["reason"]


def test_a_filing_that_parses_to_dei_only_is_not_ingested(project, edgar):
    """A public float and a share count are the cover page, not the accounting.

    In pile 1, where the form itself raises no objection, the taxonomy is the
    only thing left to refuse on -- and it does.
    """
    p, store, root = project
    out = filings.ingest(store, p, root, [
        {"company": "AAPL", "accession": "0000320193-25-000012"}])   # a 10-K, dei only
    one = out["filings"][0]
    assert out["landed"] == 0
    assert one["form"] == "10-K"
    assert one["pile"] == 1, "the form was in scope; the contents were not"
    assert one["ingested"] is False
    assert one["taxonomies"] == ["dei"]
    assert "cover page" in one["reason"]


def test_an_amendment_is_refused_on_its_pile_before_its_contents(project, edgar):
    """A `10-K/A` is pile 3. The label said `unknown`; the pile settles it."""
    p, store, root = project
    out = filings.ingest(store, p, root, [
        {"company": "AAPL", "accession": "0000320193-25-000003"}])
    one = out["filings"][0]
    assert out["landed"] == 0
    assert one["form"] == "10-K/A"
    assert one["taxonomies"] == ["dei"]
    assert "pile 3" in one["reason"]


def test_the_body_is_cached_even_for_a_filing_that_is_refused(project, edgar):
    """The fetch is paid once. Widening the scope later costs nothing."""
    p, store, root = project
    filings.ingest(store, p, root, [
        {"company": "AAPL", "accession": "0000320193-25-000010"}])
    cached = list((root / "data" / "documents").glob("*.htm.gz"))
    assert len(cached) == 1


def test_an_accession_the_company_never_filed_is_named_not_crashed(project, edgar):
    p, store, root = project
    out = filings.ingest(store, p, root, [
        {"company": "AAPL", "accession": "0000000000-00-000000"}])
    assert out["landed"] == 0
    assert "no filing with accession" in out["filings"][0]["reason"]


def test_ingesting_the_same_filing_twice_does_not_double_the_rows(project, edgar):
    p, store, root = project
    chosen = [{"company": "AAPL", "accession": "0000320193-25-000001"}]
    filings.ingest(store, p, root, chosen)
    filings.ingest(store, p, root, chosen)
    assert store.table_info("raw.documents").rows == 1


def test_nothing_to_ingest_is_told_what_to_pass(project, edgar):
    p, store, root = project
    with pytest.raises(filings.FilingsError, match="find_filings"):
        filings.ingest(store, p, root, [])


# ------------------------------------------------------ the selection is a list
def test_the_sec_connector_reads_a_universe(tmp_path, edgar):
    """A list of companies is a named list, not an array inside a source.

    `save_universe` writes `./universes/<id>.csv`, which is what makes a
    universe addressable by the name it was saved under.
    """
    from qanat.editor import save_universe
    from qanat.sources import sec

    project, store = a_project(tmp_path)
    try:
        (tmp_path / "qanat.yaml").write_text("project: filings\nstages:\n  - id: raw\n")
        save_universe(project, tmp_path,
                      {"id": "two", "index": "Two", "symbols": "./universes/two.csv",
                       "symbols_list": ["AAPL", "TM"]})
        src = Source(id="filings", to=["raw.documents"], connector="sec",
                     options={"from_universe": "two", "user_agent": UA})
        assert sec.companies_of(src, tmp_path) == ["AAPL", "TM"]

        by_path = Source(id="filings", to=["raw.documents"], connector="sec",
                         options={"from_universe": "./universes/two.csv", "user_agent": UA})
        assert sec.companies_of(by_path, tmp_path) == ["AAPL", "TM"]
    finally:
        store.close()


def test_a_sec_source_with_neither_list_says_what_to_pass(tmp_path):
    from qanat.sources import sec

    src = Source(id="filings", to=["raw.documents"], connector="sec",
                 options={"user_agent": UA})
    with pytest.raises(ValueError, match="from_universe"):
        sec.companies_of(src, tmp_path)


def test_adding_a_company_names_no_table_stage_or_connector():
    """The whole reason these two tools exist.

    Choosing filings used to mean `save_source`, whose required fields are an
    id, a target table and a connector -- so changing a list of companies meant
    restating the wiring around it.
    """
    from qanat.mcp import TOOLS

    for name in ("find_filings", "ingest_filings"):
        entry = next(t for t in TOOLS if t["name"] == name)
        must = set(entry["inputSchema"]["required"])
        assert not must & {"to", "connector", "stage", "table", "ref", "mode", "id"}, \
            f"{name} makes the caller name part of the pipeline"
    find = next(t for t in TOOLS if t["name"] == "find_filings")
    assert find["inputSchema"]["required"] == ["companies"]


# ------------------------------------------------------------- subscriptions
def a_written_project(root: Path) -> tuple[Project, Store]:
    """A project on disk, because a subscription is an edit to qanat.yaml."""
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "qanat.yaml").write_text(textwrap.dedent("""\
        project: filings
        store: ./data/qanat.duckdb
        stages:
          - id: raw
            kind: raw
        sources:
          - id: filings
            to: [raw.documents]
            connector: sec
            mode: append
            key: [id]
            options:
              companies: [AAPL]
              form: 10-K
              user_agent: qanat tests nobody@example.com
              cache: ./data/documents
        corpus:
          documents: raw.documents
    """))
    from qanat.project import load

    project, _ = load(root)
    return project, Store(project.store_url(root))


@pytest.fixture
def written(tmp_path: Path):
    p, s = a_written_project(tmp_path)
    yield p, s, tmp_path
    s.close()


def test_an_interval_and_an_hour_become_a_cron_line():
    assert filings.cron_for("daily", 6) == "0 6 * * *"
    assert filings.cron_for("monthly", 23) == "0 23 1 * *"
    assert filings.every_of("0 6 * * *") == "daily"
    assert filings.every_of("0 23 1 * *") == "monthly"
    assert filings.every_of("*/5 * * * *") is None, "a hand-written line is not an interval"
    assert filings.every_of(None) is None


def test_an_hour_that_is_not_an_hour_is_refused():
    with pytest.raises(filings.FilingsError, match="0 to 23"):
        filings.cron_for("daily", 24)
    with pytest.raises(filings.FilingsError, match="daily"):
        filings.cron_for("hourly", 6)


def test_a_subscription_survives_a_reload_and_reads_back(written, edgar):
    """Written to qanat.yaml, which is what makes it outlive the process."""
    p, store, root = written
    out = filings.subscribe(p, root, ["AAPL", "MSFT"], forms=["10-K"],
                            every="daily", hour=7)
    assert out["schedule"] == "0 7 * * *"

    from qanat.project import load

    fresh, _ = load(root)
    back = filings.subscriptions(store, fresh)
    mine = next(r for r in back["subscriptions"] if r["id"] == out["subscribed"])
    assert mine["companies"] == ["AAPL", "MSFT"]
    assert mine["forms"] == ["10-K"]
    assert mine["schedule"] == "0 7 * * *"
    assert mine["every"] == "daily"
    assert mine["next_due"], "a subscription with no next time is not a subscription"


def test_list_steps_reads_the_cron_line_the_tool_wrote(written, edgar):
    """A narrow writer over machinery that already exists, and this proves it."""
    p, _store, root = written
    out = filings.subscribe(p, root, ["AAPL"], forms=["10-Q"], every="monthly", hour=3)

    from qanat.mcp import TOOLS, Session

    session = Session(str(root))
    try:
        listed = next(x for x in TOOLS if x["name"] == "list_steps")["handler"](session, {})
        mine = next(s for s in listed["sources"] if s["id"] == out["subscribed"])
        assert mine["schedule"] == "0 3 1 * *"
        assert mine["options"]["form"] == "10-Q"
    finally:
        session.close()


def test_the_same_selection_is_the_same_subscription(written, edgar):
    """Subscribing twice must not leave two of them running."""
    p, _store, root = written
    first = filings.subscribe(p, root, ["AAPL"], forms=["10-K"], hour=6)
    again = filings.subscribe(p, root, ["AAPL"], forms=["10-K"], hour=9)
    assert first["subscribed"] == again["subscribed"] == "filings"
    assert len(p.sources) == 1, "a second poll on the same table is not allowed"
    assert p.sources[0].schedule == "0 9 * * *", "the second call replaced the first"


def test_a_form_nothing_can_parse_is_refused_at_subscribe_time(written, edgar):
    """A month of rows nothing reads is worse than a refusal now."""
    p, _store, root = written
    with pytest.raises(filings.FilingsError, match="pile 2"):
        filings.subscribe(p, root, ["AAPL"], forms=["20-F"])
    with pytest.raises(filings.FilingsError, match="find_filings"):
        filings.subscribe(p, root, ["AAPL"], forms=["10-K", "8-K"])


def test_no_clock_is_attached_on_a_stdio_server(written, edgar):
    """The failure this task exists to prevent: being told it will happen."""
    p, store, root = written
    filings.subscribe(p, root, ["AAPL"], forms=["10-K"])
    out = filings.subscriptions(store, p, sched=None)
    assert out["clock"]["attached"] is False
    assert out["clock"]["what_runs_them"] is None
    assert "dies with the conversation" in out["clock"]["why"]
    assert "qanat schedule" in out["clock"]["why"]


def test_the_scheduler_is_reported_when_one_is_running(written, edgar):
    """A served process outlives the request, and `--schedule` gives it a clock."""
    from qanat.scheduler import Scheduler

    p, store, root = written
    filings.subscribe(p, root, ["AAPL"], forms=["10-K"], hour=5)
    sched = Scheduler(store, p, root)
    out = filings.subscriptions(store, p, sched=sched)
    assert out["clock"]["attached"] is True
    assert out["clock"]["what_runs_them"]
    mine = out["subscriptions"][0]
    assert mine["next_due"], "the scheduler knows when this is next due"
    assert mine["running_now"] is False


def test_a_subscription_ingests_what_appears_after_it_and_not_outside_its_period(
        written, edgar, monkeypatch):
    """The selection points forward, and the period still bounds it."""
    from qanat.runner import run_source
    from qanat.sources import sec

    p, store, root = written
    out = filings.subscribe(p, root, ["AAPL"], forms=["10-K"],
                            frm="2025-01-01", to="2025-12-31", years=10)
    src = next(s for s in p.sources if s.id == out["subscribed"])

    monkeypatch.setattr(sec.Throttle, "wait", lambda self: None)
    first = run_source(store, p, root, src)
    assert first.ok
    # Two 10-Ks report on a 2025 period; the 2020 one is outside the window.
    landed = store.rows("raw.documents", columns="id")["id"].tolist()
    assert sorted(landed) == ["0000320193-25-000001", "0000320193-25-000012"]
    assert "0000320193-20-000001" not in landed

    # A filing that appears later lands on the next pass, and nothing doubles.
    FILINGS.append(("0000320193-25-000013", "2025-12-20", "2025-09-27", "10-K"))
    BODIES["0000320193-25-000013"] = tagged("us-gaap", "Revenues")
    try:
        second = run_source(store, p, root, src)
        assert second.ok
        after = store.rows("raw.documents", columns="id")["id"].tolist()
        assert "0000320193-25-000013" in after
        assert len(after) == 3, "an append with a key does not duplicate the first two"
    finally:
        FILINGS.pop()
        BODIES.pop("0000320193-25-000013")


def test_unsubscribing_leaves_the_rows_alone(written, edgar, monkeypatch):
    from qanat.runner import run_source
    from qanat.sources import sec

    p, store, root = written
    out = filings.subscribe(p, root, ["AAPL"], forms=["10-K"], frm="2025-01-01")
    src = next(s for s in p.sources if s.id == out["subscribed"])
    monkeypatch.setattr(sec.Throttle, "wait", lambda self: None)
    run_source(store, p, root, src)
    before = store.table_info("raw.documents").rows
    assert before > 0

    filings.unsubscribe(p, root, out["subscribed"])
    assert not any(s.id == out["subscribed"] for s in p.sources)
    assert store.table_info("raw.documents").rows == before


def test_unsubscribing_something_that_is_not_subscribed_says_so(written):
    p, _store, root = written
    with pytest.raises(filings.FilingsError, match="no source"):
        filings.unsubscribe(p, root, "sub_nothing")


def test_the_docs_no_longer_claim_a_scheduler_is_about_authority():
    """It was "not something an agent should do mid-question", which states a
    rule about authority where the truth is a fact about process lifetime."""
    doc = (Path(__file__).resolve().parents[1] / "docs" / "agents.md").read_text()
    assert "mid-question" not in doc
    assert "process lifetime" in doc


def test_one_subscription_per_corpus_because_a_table_has_one_producer(written, edgar):
    """Not a simplification -- the stage contract refuses the alternative.

    Two sources writing one table means no run row can say which pass landed a
    given document. So the subscription *is* the producer of the documents
    table, and several forms ride in one selection rather than one per corpus.
    """
    p, _store, root = written
    out = filings.subscribe(p, root, ["AAPL"], forms=["10-K", "10-Q"], hour=4)
    assert out["subscribed"] == "filings", "it took over the existing producer"
    assert out["forms"] == ["10-K", "10-Q"]
    assert len(p.sources) == 1

    from qanat.project import validate
    from qanat.sources import sec

    assert validate(p, root).ok, "the project still holds against the contract"
    assert sec.forms_of(p.sources[0]) == ["10-K", "10-Q"]


def test_a_subscription_over_two_forms_lands_both_and_labels_each_one(
        written, edgar, monkeypatch):
    """`doc_type` is the row's own form, not the source's list."""
    from qanat.runner import run_source
    from qanat.sources import sec

    p, store, root = written
    filings.subscribe(p, root, ["AAPL"], forms=["10-K", "10-Q"],
                      frm="2025-01-01", years=10)
    monkeypatch.setattr(sec.Throttle, "wait", lambda self: None)
    assert run_source(store, p, root, p.sources[0]).ok

    kinds = store.rows("raw.documents", columns="doc_type")["doc_type"].tolist()
    assert sorted(set(kinds)) == ["10-K", "10-Q"]


def test_a_corpus_fed_by_another_connector_is_not_taken_over(tmp_path, edgar):
    """A csv-fed documents table is somebody's own pipeline, not an EDGAR poll."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "seed" / "docs.csv").write_text("id\n1\n")
    (tmp_path / "qanat.yaml").write_text(textwrap.dedent("""\
        project: filings
        store: ./data/qanat.duckdb
        stages:
          - id: raw
            kind: raw
        sources:
          - id: docs
            to: [raw.documents]
            connector: csv
            options:
              path: ./seed/docs.csv
        corpus:
          documents: raw.documents
    """))
    from qanat.project import load

    project, _ = load(tmp_path)
    store = Store(project.store_url(tmp_path))
    try:
        with pytest.raises(filings.FilingsError, match="csv source"):
            filings.subscribe(project, tmp_path, ["AAPL"], forms=["10-K"])
    finally:
        store.close()


def test_both_of_edgars_spellings_of_the_schedule_thirteen_forms_are_known():
    """A live submissions index on 2026-10-31 carried `SCHEDULE 13G` on recent
    filings and `SC 13G` on older ones. Same form, two spellings, and only one
    of them was in the table -- so the other read as an unmeasured form."""
    for spelling in ("SCHEDULE 13G", "SC 13G", "SCHEDULE 13D", "SC 13D"):
        assert filings.pile(spelling) == 4, spelling
        assert filings.label(spelling) == "cover_only"


def test_no_sec_request_goes_out_of_find_filings_without_a_contact(project, monkeypatch):
    """The rule holds at the new door too: refused before the request, not after.

    A shared default would put every caller behind one identity, and the SEC
    refuses an anonymous request anyway.
    """
    p, store, _root = project
    p.sources[0].options.pop("user_agent", None)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    asked: list[str] = []
    monkeypatch.setattr(filings.httpx, "Client",
                        lambda *a, **kw: asked.append("opened") or None)
    with pytest.raises(ValueError, match="no contact"):
        filings.find(store, p, ["AAPL"])
    assert asked == [], "a client was opened before the contact was checked"


def test_a_subscriptions_last_run_reports_the_rows_it_landed(written, edgar, monkeypatch):
    """It read `rows` and the runs table calls it `rows_out`, so every pass that
    had landed rows reported None -- in the one tool whose whole job is saying
    honestly what has and has not happened."""
    from qanat.runner import run_source
    from qanat.sources import sec

    p, store, root = written
    filings.subscribe(p, root, ["AAPL"], forms=["10-K"], frm="2025-01-01")
    monkeypatch.setattr(sec.Throttle, "wait", lambda self: None)
    result = run_source(store, p, root, p.sources[0])
    assert result.rows > 0

    mine = filings.subscriptions(store, p)["subscriptions"][0]
    assert mine["last_run"]["status"] == "ok"
    assert mine["last_run"]["rows"] == result.rows, "the rows it actually landed"
    assert mine["last_run"]["ended_at"], "and when it finished"


def test_a_subscription_that_has_never_run_says_so(written, edgar):
    p, store, root = written
    filings.subscribe(p, root, ["AAPL"], forms=["10-K"])
    mine = filings.subscriptions(store, p)["subscriptions"][0]
    assert mine["last_run"] is None
    assert mine["next_due"], "and still says when it is due"
