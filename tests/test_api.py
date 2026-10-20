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

    def test_perps_http_funding_and_gap_liquidation(self):
        def post(route, **body):
            status, result = self.request("/v1/perps/" + route, body)
            self.assertEqual(status, 200, result)
            return result
        for owner in ("long", "short"):
            post("accounts/funding", request_id="deposit-"+owner, owner=owner, amount=500000, now=0)
        post("insurance", request_id="insurance", amount=10000, now=0)
        post("oracle", request_id="o1", index_price=10000, mark_price=10000, observed_at=0, now=0)
        opened = post("open", request_id="open", long_owner="long", short_owner="short", quantity=10, long_margin=20000, short_margin=20000, now=0)
        pair = opened["result"]["pair_id"]
        self.assertFalse(post("risk", pair_id=pair, now=0)["short_liquidatable"])
        post("oracle", request_id="o2", index_price=10000, mark_price=10000, observed_at=3600, now=3600)
        funding = post("funding", request_id="f1", epoch=1, rate_bps=10, now=3600)
        self.assertEqual(funding["result"]["payments"][0]["long_paid"], 100)
        post("oracle", request_id="o3", index_price=15000, mark_price=15000, observed_at=3601, now=3601)
        result = post("liquidate", request_id="liq", pair_id=pair, now=3601)
        self.assertEqual(result["result"]["insurance_used"], 10000)
        self.assertEqual(result["result"]["uncovered_loss"], 19900)
        self.assertEqual(result, post("liquidate", request_id="liq", pair_id=pair, now=3601))
        status, audit = self.request("/v1/perps/audit")
        self.assertEqual(status, 200)
        self.assertEqual(audit["conservation"], "passed")
        self.assertEqual(audit["state_commitment"], "passed")
        self.assertEqual(audit["held_collateral"], 1010000)

    def test_perps_http_rejects_stale_oracle_without_mutation(self):
        self.request("/v1/perps/oracle", dict(request_id="o", index_price=10000, mark_price=10000, observed_at=0, now=0))
        before = self.request("/v1/perps/state")[1]
        status, _ = self.request("/v1/perps/open", dict(request_id="open", long_owner="a", short_owner="b", quantity=10, long_margin=20000, short_margin=20000, now=61))
        self.assertEqual(status, 400)
        self.assertEqual(before, self.request("/v1/perps/state")[1])

    def test_perps_http_requires_operator_token(self):
        self.assertEqual(self.request("/v1/perps/state", auth=False)[0], 401)
        self.assertEqual(self.request("/v1/perps/accounts/funding", dict(request_id="d", owner="a", amount=1, now=0), auth=False)[0], 401)

    def test_perps_http_rejects_invalid_wire_values(self):
        for body in (dict(request_id="d", owner="a", amount=True, now=0),
                     dict(request_id="d", owner="a", amount=1, now=0, extra=1)):
            self.assertEqual(self.request("/v1/perps/accounts/funding", body)[0], 400)
        self.assertEqual(self.request("/v1/perps/risk", dict(pair_id={}, now=0))[0], 400)

    def test_perps_http_limit_matching_funding_and_partial_close(self):
        def post(route,**body):
            status,result=self.request("/v1/perps/"+route,body)
            self.assertEqual(status,200,result)
            return result
        for owner in ("a","b"):
            post("accounts/funding",request_id="fund-"+owner,owner=owner,amount=500000,now=0)
        post("oracle",request_id="o1",index_price=10000,mark_price=10000,observed_at=0,now=0)
        for owner,side,price in (("a","long",10100),("b","short",9900)):
            post("orders",request_id="order-"+owner,owner=owner,nonce="n",side=side,quantity=5,
                 limit_price=price,margin_per_lot=3000,expires_at=10000,now=0)
        match=post("match",request_id="match",now=0)
        self.assertEqual(match["result"]["matched_quantity"],5)
        self.assertEqual(match,post("match",request_id="match",now=0))
        pair=match["result"]["matches"][0]["pair_id"]
        post("oracle",request_id="o2",index_price=10051,mark_price=10000,observed_at=3600,now=3600)
        post("funding",request_id="f",epoch=1,rate_bps=100,now=3600)
        post("oracle",request_id="o3",index_price=10500,mark_price=10500,observed_at=3601,now=3601)
        reduced=post("reduce",request_id="reduce",pair_id=pair,owner="a",quantity=2,now=3601)
        self.assertEqual(reduced["result"]["remaining_quantity"],3)
        self.assertEqual(reduced["result"]["funding_long_paid"],200)
        self.assertEqual(reduced["result"]["long_realized_pnl"],1000)
        self.assertEqual(reduced,post("reduce",request_id="reduce",pair_id=pair,owner="a",quantity=2,now=3601))
        post("close",request_id="close",pair_id=pair,owner="b",now=3601)
        _,audit=self.request("/v1/perps/audit")
        self.assertEqual(audit["conservation"],"passed")
        self.assertEqual(audit["held_collateral"],1000000)

    def test_perps_http_cancel_order_and_reject_unauthorized_orders(self):
        self.assertEqual(self.request("/v1/perps/orders",{},auth=False)[0],401)
        self.request("/v1/perps/accounts/funding",dict(request_id="f",owner="a",amount=50000,now=0))
        status,placed=self.request("/v1/perps/orders",dict(request_id="o",owner="a",nonce="n",side="long",
                                           quantity=1,limit_price=10000,margin_per_lot=3000,expires_at=1000,now=0))
        self.assertEqual(status,200)
        oid=placed["result"]["id"]
        self.assertEqual(self.request("/v1/perps/orders/cancel",dict(request_id="bad",order_id=oid,owner="wrong",now=0))[0],400)
        status,result=self.request("/v1/perps/orders/cancel",dict(request_id="cancel",order_id=oid,owner="a",now=100))
        self.assertEqual(status,200)
        self.assertEqual(result["result"]["status"],"cancelled")
        self.assertEqual(self.request("/v1/perps/state")[1]["accounts"][0]["cash"],50000)
