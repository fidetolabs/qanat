"""Tiny JSON API for poking the rest connector."""
import json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LIST = [{"date": "2024-01-01", "symbol": "A", "close": 100},
        {"date": "2024-01-02", "symbol": "A", "close": 101}]
NESTED = {"status": "ok", "data": {"items": LIST}}
INDEXED = {"2024-01-02": {"EUR": 0.91, "JPY": 148.0},
           "2024-01-01": {"EUR": 0.90, "JPY": 147.0}}
PER_SYM = {"A": [{"date": "2024-01-01", "close": 100}],
           "B": [{"date": "2024-01-01", "close": 200}]}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        p = self.path.split("?")[0]
        auth = self.headers.get("Authorization")
        if p == "/list":       return self._send(200, LIST)
        if p == "/nested":     return self._send(200, NESTED)
        if p == "/indexed":    return self._send(200, INDEXED)
        if p == "/dict":       return self._send(200, {"date": "2024-01-01", "symbol": "A", "close": 100})
        if p == "/scalar":     return self._send(200, 42)
        if p == "/nulllist":   return self._send(200, None)
        if p == "/boom":       return self._send(500, {"error": "internal"})
        if p == "/notjson":    return self._send(200, "<html>nope</html>", "text/html")
        if p == "/echoauth":   return self._send(200, [{"auth": str(auth), "path": self.path}])
        if p == "/needauth":
            if auth != "Bearer SECRET":
                return self._send(401, {"error": "unauthorized"})
            return self._send(200, LIST)
        if p.startswith("/sym/"):
            s = p.rsplit("/", 1)[-1]
            if s in PER_SYM: return self._send(200, PER_SYM[s])
            return self._send(404, {"error": f"no symbol {s}"})
        if p.startswith("/half/"):
            s = p.rsplit("/", 1)[-1]
            if s == "A": return self._send(200, PER_SYM["A"])
            return self._send(404, {"error": f"no symbol {s}"})
        return self._send(404, {"error": "no route"})

def serve(port=8732):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
