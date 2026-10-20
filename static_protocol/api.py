"""Loopback-only, token-protected operator API for the local simulator."""

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import urlsplit

from .types import ProtocolError, canonical
from . import __version__
from .perps import PerpetualEngine


def make_server(engine, token, port=8787):
    if not isinstance(token, str) or len(token) < 32:
        raise ProtocolError("operator token must be at least 32 characters")
    perps = PerpetualEngine(engine.path)

    class Handler(BaseHTTPRequestHandler):
        server_version = "STATIC-Simulator/" + __version__

        def log_message(self, fmt, *args):
            pass  # Never log bearer tokens or request bodies.

        def respond(self, status, body):
            raw = canonical(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self):
            supplied = self.headers.get("Authorization", "").encode()
            return hmac.compare_digest(supplied, f"Bearer {token}".encode())

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/health":
                self.respond(200, {"mode": "simulation", "live_funds": False, "version": __version__})
                return
            if not self.authorized():
                self.respond(401, {"error": "operator token required"})
                return
            try:
                if path == "/v1/state":
                    self.respond(200, engine.state())
                elif path == "/v1/audit":
                    self.respond(200, engine.audit())
                elif path == "/v1/perps/state":
                    self.respond(200, perps.state())
                elif path == "/v1/perps/audit":
                    self.respond(200, perps.audit())
                else:
                    self.respond(404, {"error": "unknown route"})
            except ProtocolError as exc:
                self.respond(409, {"error": str(exc)})

        def do_POST(self):
            if not self.authorized():
                self.respond(401, {"error": "operator token required"})
                return
            try:
                length = self.headers.get("Content-Length", "")
                if not length.isdecimal() or not 1 <= int(length) <= 65536 or self.headers.get("Transfer-Encoding"):
                    raise ProtocolError("Content-Length must be 1-65536 bytes; chunked requests unsupported")
                if self.headers.get_content_type() != "application/json":
                    raise ProtocolError("Content-Type must be application/json")
                data = json.loads(self.rfile.read(int(length)))
                if type(data) is not dict:
                    raise ProtocolError("JSON object required")
                routes = {"/v1/funding": (engine.fund, {"owner", "asset", "amount", "direction"}),
                          "/v1/orders": (engine.submit, {"owner", "nonce", "side", "quantity", "limit_price", "expiry_daa", "fee_rail"}),
                          "/v1/cancel": (engine.cancel, {"owner", "order_id"}),
                          "/v1/sweeps": (engine.sweep, {"request_id", "daa", "max_inputs"}),
                          "/v1/perps/accounts/funding": (perps.fund_account, {"request_id", "owner", "amount", "now", "direction"}),
                          "/v1/perps/insurance": (perps.fund_insurance, {"request_id", "amount", "now"}),
                          "/v1/perps/oracle": (perps.publish_oracle, {"request_id", "index_price", "mark_price", "observed_at", "now"}),
                          "/v1/perps/open": (perps.open_pair, {"request_id", "long_owner", "short_owner", "quantity", "long_margin", "short_margin", "now", "long_rail", "short_rail"}),
                          "/v1/perps/top-up": (perps.top_up, {"request_id", "pair_id", "owner", "amount", "now"}),
                          "/v1/perps/funding": (perps.settle_funding, {"request_id", "epoch", "rate_bps", "now"}),
                          "/v1/perps/close": (perps.close_pair, {"request_id", "pair_id", "owner", "now"}),
                          "/v1/perps/liquidate": (perps.liquidate, {"request_id", "pair_id", "now"}),
                          "/v1/perps/risk": (perps.risk, {"pair_id", "now"})}
                path = urlsplit(self.path).path
                if path not in routes:
                    self.respond(404, {"error": "unknown route"})
                    return
                fn, allowed = routes[path]
                if set(data) - allowed:
                    raise ProtocolError("unknown request fields")
                self.respond(200, fn(**data))
            except (ProtocolError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.respond(400, {"error": str(exc)})
            except Exception:
                self.respond(500, {"error": "internal simulator failure; transaction not acknowledged"})

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def get_request(self):
            sock, addr = super().get_request()
            sock.settimeout(10)
            return sock, addr

    return Server(("127.0.0.1", port), Handler)
