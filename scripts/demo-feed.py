#!/usr/bin/env python3
"""Mock market feed for examples/scheduled-ingest — new bars on every live poll.

Endpoints:
  GET /health
  GET /v1/bars/history   one-shot bootstrap (~45 sessions × 8 symbols)
  GET /v1/bars/live      one new session per request (append ingest)
"""

from __future__ import annotations

import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "JPM", "XOM", "SPY"]
BASE = {
    "AAPL": 190.0,
    "MSFT": 420.0,
    "NVDA": 880.0,
    "AMZN": 185.0,
    "META": 510.0,
    "JPM": 195.0,
    "XOM": 115.0,
    "SPY": 540.0,
}


class Market:
    def __init__(self) -> None:
        self.prices = dict(BASE)
        self.vol = {s: 0.012 + (hash(s) % 7) * 0.001 for s in SYMBOLS}
        self.session = 0
        self._history_sent = False

    def _bar(self, symbol: str, ts: datetime) -> dict:
        px = self.prices[symbol]
        ret = random.gauss(0.0004, self.vol[symbol])
        px = max(1.0, px * (1 + ret))
        self.prices[symbol] = px
        hi = px * (1 + abs(random.gauss(0, self.vol[symbol] / 3)))
        lo = px * (1 - abs(random.gauss(0, self.vol[symbol] / 3)))
        op = px / (1 + ret)
        return {
            "t": ts.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "s": symbol,
            "o": round(op, 4),
            "h": round(hi, 4),
            "l": round(lo, 4),
            "c": round(px, 4),
            "v": int(1_000_000 * (0.7 + abs(random.gauss(0.3, 0.2)))),
        }

    def history(self) -> list[dict]:
        rows: list[dict] = []
        end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        for day in range(45):
            ts = end - timedelta(days=44 - day)
            for sym in SYMBOLS:
                rows.append(self._bar(sym, ts))
        self._history_sent = True
        return rows

    def live(self) -> list[dict]:
        self.session += 1
        ts = datetime.now(timezone.utc)
        return [self._bar(sym, ts) for sym in SYMBOLS]


MARKET = Market()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stdout.write(f"[demo-feed] {self.address_string()} {fmt % args}\n")
        sys.stdout.flush()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._json({"ok": True, "session": MARKET.session})
        elif path == "/v1/bars/history":
            rows = MARKET.history()
            self._json({"data": rows, "kind": "history", "rows": len(rows)})
        elif path == "/v1/bars/live":
            rows = MARKET.live()
            self._json({"data": rows, "kind": "live", "session": MARKET.session, "rows": len(rows)})
        else:
            self.send_error(404)

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    port = int(__import__("os").environ.get("DEMO_FEED_PORT", "8765"))
    host = __import__("os").environ.get("DEMO_FEED_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"demo-feed listening on http://{host}:{port}", flush=True)
    print("  history → /v1/bars/history", flush=True)
    print("  live    → /v1/bars/live", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\ndemo-feed stopped", flush=True)


if __name__ == "__main__":
    main()
