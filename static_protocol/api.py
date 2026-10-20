"""Loopback-only, token-protected operator API for the local simulator."""

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import urlsplit

from .types import ProtocolError, canonical


def make_server(engine, token, port=8787):
    if not isinstance(token, str) or len(token) < 32:
        raise ProtocolError("operator token must be at least 32 characters")

    class Handler(BaseHTTPRequestHandler):
        server_version = "STATIC-Simulator/0.1"

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
                self.respond(200, {"mode": "simulation", "live_funds": False, "version": "0.1.0"})
                return
            if not self.authorized():
                self.respond(401, {"error": "operator token required"})
                return
            try:
                if path == "/v1/state":
                    self.respond(200, engine.state())
                elif path == "/v1/audit":
                    self.respond(200, engine.audit())
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
                          "/v1/sweeps": (engine.sweep, {"request_id", "daa", "max_inputs"})}
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
