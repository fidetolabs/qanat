"""What a connector brings in, and in what shape."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from qanat.models import Source
from qanat.sources import rest

#: every path the test server was asked for, in order
ASKED: list[str] = []


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        ASKED.append(self.path)
        sym = self.path.split("/chart/")[-1].split("?")[0]
        # the shape that motivated `payload:` -- columns held as parallel arrays,
        # which no records/orient/rename combination can flatten
        body = {"chart": {"result": [{"meta": {"symbol": sym},
                                      "timestamp": [1, 2],
                                      "indicators": {"quote": [{"close": [10.0, 11.0]}]}}]}}
        out = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):              # keep pytest output clean
        return


@pytest.fixture
def feed():
    ASKED.clear()
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _source(url: str, **options) -> Source:
    return Source.model_validate({
        "id": "px", "to": ["raw.chart"], "connector": "rest",
        "options": {"url": url, **options},
    })


def test_one_request_per_symbol_stacked_into_one_table(feed, tmp_path: Path):
    src = _source(feed + "/chart/{symbol}", symbols=["AAPL", "MSFT"], payload=True)
    df = rest.fetch(src, tmp_path)

    assert [p.split("/chart/")[-1] for p in ASKED] == ["AAPL", "MSFT"]
    assert list(df["symbol"]) == ["AAPL", "MSFT"], "the symbol asked for is kept"
    assert len(df) == 2


def test_payload_lands_the_body_untouched(feed, tmp_path: Path):
    """`raw` means landed as it arrived. A step takes it apart, not the connector."""
    src = _source(feed + "/chart/AAPL", payload=True)
    df = rest.fetch(src, tmp_path)

    assert list(df.columns) == ["fetched_at", "source_id", "symbol", "payload"]
    body = json.loads(df["payload"].iloc[0])
    # the parallel arrays survive, which is the whole point
    assert body["chart"]["result"][0]["timestamp"] == [1, 2]
    assert body["chart"]["result"][0]["indicators"]["quote"][0]["close"] == [10.0, 11.0]


def test_the_envelope_carries_a_time_column(feed, tmp_path: Path):
    """Without one, retention and the replay's as-of views both skip the table."""
    from qanat.retention import _TIME_COLS
    from qanat.store import TIME_COLS

    df = rest.fetch(_source(feed + "/chart/AAPL", payload=True), tmp_path)
    assert "fetched_at" in df.columns
    assert "fetched_at" in TIME_COLS and "fetched_at" in _TIME_COLS


def test_flattening_still_works_when_no_payload_is_asked_for(feed, tmp_path: Path):
    """The default did not move: a source with a clean shape lands columns."""
    src = _source(feed + "/chart/AAPL", records="chart.result")
    df = rest.fetch(src, tmp_path)
    assert "timestamp" in df.columns
    assert "payload" not in df.columns


def test_symbols_can_come_from_a_universe_file(feed, tmp_path: Path):
    (tmp_path / "u.csv").write_text("symbol,name\nAAPL,Apple\nMSFT,Microsoft\n")
    src = _source(feed + "/chart/{symbol}", from_universe="u.csv", payload=True)
    df = rest.fetch(src, tmp_path)
    assert list(df["symbol"]) == ["AAPL", "MSFT"]


def test_symbol_reaches_params_and_headers_too(feed, tmp_path: Path):
    src = _source(feed + "/chart/x", symbols=["AAPL"], params={"s": "{symbol}"}, payload=True)
    rest.fetch(src, tmp_path)
    assert "s=AAPL" in ASKED[0]
