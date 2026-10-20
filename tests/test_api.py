from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import json

from static_protocol.api import make_server
from static_protocol.engine import Engine
from static_protocol.types import QUOTE, BASE


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.token = "test-token-" + "a" * 32
        self.server = make_server(Engine(Path(self.temp.name) / "api.sqlite"), self.token, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(self, path, body=None, auth=True):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.url + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_unauthorized_requests_rejected(self):
        self.assertEqual(self.request("/v1/state", auth=False)[0], 401)
        self.assertEqual(self.request("/v1/funding", {"owner": "a", "asset": QUOTE, "amount": 100}, auth=False)[0], 401)
        self.assertFalse(self.request("/health", auth=False)[1]["live_funds"])

    def test_complete_http_settlement_and_audit(self):
        for owner, asset, amount in (("buyer", QUOTE, 100000), ("seller", BASE, 100)):
            self.assertEqual(self.request("/v1/funding", dict(owner=owner, asset=asset, amount=amount))[0], 200)
        for owner, side, price in (("buyer", "buy", 110), ("seller", "sell", 100)):
            status, _ = self.request("/v1/orders", dict(owner=owner, nonce="n", side=side, quantity=10, limit_price=price, expiry_daa=100))
            self.assertEqual(status, 200)
        status, receipt = self.request("/v1/sweeps", dict(request_id="batch", daa=1))
        self.assertEqual(status, 200)
        self.assertEqual(receipt["executed_volume"], 10)
        self.assertEqual(self.request("/v1/sweeps", dict(request_id="batch", daa=1))[1], receipt)
        self.assertEqual(self.request("/v1/audit")[1]["conservation"], "passed")

    def test_cancel_over_http(self):
        self.request("/v1/funding", dict(owner="a", asset=QUOTE, amount=1000))
        _, order = self.request("/v1/orders", dict(owner="a", nonce="n", side="buy", quantity=1, limit_price=100, expiry_daa=100))
        self.assertEqual(self.request("/v1/cancel", dict(owner="a", order_id=order["id"]))[1]["status"], "cancelled")

    def test_invalid_input_and_unknown_fields(self):
        for body in ([], {"owner": "a", "asset": QUOTE, "amount": True}, {"owner": "a", "asset": QUOTE, "amount": 1, "extra": 1}):
            self.assertEqual(self.request("/v1/funding", body)[0], 400)
        self.assertEqual(self.request("/nonexistent")[0], 404)

    def test_short_operator_token_rejected(self):
        from static_protocol.types import ProtocolError
        with self.assertRaises(ProtocolError):
            make_server(None, "short", 0)
