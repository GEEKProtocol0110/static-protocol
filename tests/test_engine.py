from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from static_protocol.engine import Engine
from static_protocol.types import BASE, QUOTE, ProtocolError, buy_reserve, fee
from static_protocol.auction import clear


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.sqlite"
        self.engine = Engine(self.path)

    def fund(self):
        self.engine.fund("buyer", QUOTE, 1_000_000)
        self.engine.fund("seller", BASE, 1000)

    def orders(self, buy=10, sell=10, bp=110, sp=100, expiry=100):
        self.fund()
        b = self.engine.submit("buyer", "b", "buy", buy, bp, expiry, "native")
        s = self.engine.submit("seller", "s", "sell", sell, sp, expiry)
        return b, s

    def balance(self, owner, asset):
        return next(b for b in self.engine.state()["balances"] if b["owner"] == owner and b["asset"] == asset)

    def test_uniform_clearing_and_conservation(self):
        b, s = self.orders()
        r = self.engine.sweep("one", 1)
        self.assertEqual(r["executed_volume"], 10)
        self.assertEqual({f["price"] for f in r["fills"]}, {100})
        self.assertEqual(self.balance("buyer", BASE)["available"], 10)
        self.assertEqual(self.balance("seller", BASE)["available"], 990)
        self.assertEqual(self.balance("buyer", QUOTE)["locked"], 0)
        self.assertEqual(self.engine.audit()["conservation"], "passed")

    def test_partial_fills_keep_sufficient_reserve(self):
        self.orders(buy=20, sell=5)
        r = self.engine.sweep("one", 1)
        self.assertEqual(r["executed_volume"], 5)
        b = next(o for o in self.engine.state()["orders"] if o["side"] == "buy")
        self.assertEqual(b["remaining"], 15)
        self.assertEqual(b["reserved"], buy_reserve(15, 110, "native"))
        self.engine.cancel("buyer", b["id"])
        self.assertEqual(self.balance("buyer", QUOTE)["locked"], 0)
        self.engine.audit()

    def test_rounding_under_many_small_partial_fills(self):
        self.engine.fund("buyer", QUOTE, 20)
        self.engine.fund("seller", BASE, 10)
        self.engine.submit("buyer", "b", "buy", 10, 1, 1000)
        for i in range(10):
            self.engine.submit("seller", f"s{i}", "sell", 1, 1, 1000)
            self.engine.sweep(f"sweep{i}", i)
        self.assertEqual(self.balance("buyer", BASE)["available"], 10)
        self.assertEqual(self.balance("buyer", QUOTE)["available"], 0)
        self.engine.audit()

    def test_non_crossing_book_does_not_trade(self):
        self.orders(bp=90, sp=100)
        r = self.engine.sweep("none", 1)
        self.assertIsNone(r["clearing_price"])
        self.assertEqual(r["fills"], [])

    def test_rejects_insufficient_collateral_without_changes(self):
        self.engine.fund("buyer", QUOTE, 100)
        before = self.engine.state()
        with self.assertRaises(ProtocolError):
            self.engine.submit("buyer", "b", "buy", 1, 100, 10)
        self.assertEqual(before, self.engine.state())

    def test_nonce_retry_is_idempotent_but_changed_intent_rejected(self):
        self.fund()
        b = self.engine.submit("buyer", "n", "buy", 1, 100, 10)
        self.assertEqual(b, self.engine.submit("buyer", "n", "buy", 1, 100, 10))
        with self.assertRaises(ProtocolError):
            self.engine.submit("buyer", "n", "buy", 2, 100, 10)
        self.engine.cancel("buyer", b["id"])
        with self.assertRaises(ProtocolError):
            self.engine.submit("buyer", "n", "buy", 2, 100, 10)

    def test_expiry_boundary_refunds_without_execution(self):
        self.orders(expiry=5)
        r = self.engine.sweep("expire", 5)
        self.assertEqual(r["executed_volume"], 0)
        self.assertEqual(len(r["expired_ids"]), 2)
        self.assertEqual(self.balance("buyer", QUOTE)["available"], 1_000_000)
        self.assertTrue(all(o["status"] == "expired" for o in self.engine.state()["orders"]))

    def test_past_order_rejected_and_daa_monotonic(self):
        self.engine.sweep("empty", 5)
        self.fund()
        with self.assertRaises(ProtocolError):
            self.engine.submit("buyer", "n", "buy", 1, 100, 5)
        with self.assertRaises(ProtocolError):
            self.engine.sweep("backward", 4)

    def test_sweep_idempotence_and_parameter_conflict(self):
        self.orders()
        r = self.engine.sweep("one", 1)
        state = self.engine.state()
        self.assertEqual(r, self.engine.sweep("one", 1))
        self.assertEqual(state, self.engine.state())
        with self.assertRaises(ProtocolError):
            self.engine.sweep("one", 2)

    def test_wrong_owner_cannot_cancel(self):
        b, _ = self.orders()
        before = self.engine.state()
        with self.assertRaises(ProtocolError):
            self.engine.cancel("someone", b["id"])
        self.assertEqual(before, self.engine.state())

    def test_cancel_retry_and_cancel_after_fill(self):
        b, s = self.orders()
        first = self.engine.cancel("buyer", b["id"])
        self.assertEqual(first, self.engine.cancel("buyer", b["id"]))
        self.engine.submit("buyer", "b2", "buy", 10, 110, 100)
        self.engine.sweep("one", 1)
        with self.assertRaises(ProtocolError):
            self.engine.cancel("seller", s["id"])

    def test_withdrawal_cannot_spend_locked_funds(self):
        self.orders()
        available = self.balance("buyer", QUOTE)["available"]
        with self.assertRaises(ProtocolError):
            self.engine.fund("buyer", QUOTE, available + 1, "out")
        self.engine.fund("buyer", QUOTE, available, "out")
        self.engine.audit()

    def test_restart_retains_state_and_receipts(self):
        self.orders()
        self.engine.sweep("one", 1)
        reopened = Engine(self.path)
        self.assertEqual(self.engine.state(), reopened.state())
        self.assertEqual(self.engine.audit(), reopened.audit())

    def test_transaction_rolls_back_mid_settlement_failure(self):
        self.orders()
        before = self.engine.state()
        with patch.object(self.engine, "_assert_invariants", side_effect=RuntimeError("fault injection")):
            with self.assertRaises(RuntimeError):
                self.engine.sweep("one", 1)
        self.assertEqual(before, self.engine.state())
        self.assertEqual(self.engine.sweep("one", 1)["executed_volume"], 10)

    def test_concurrent_intake_cannot_double_spend(self):
        self.engine.fund("buyer", QUOTE, 1000)
        def submit(i):
            try:
                self.engine.submit("buyer", str(i), "buy", 1, 600, 10)
                return True
            except ProtocolError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(sum(results), 1)
        self.engine.audit()

    def test_concurrent_sweep_retry_settles_once(self):
        self.orders()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.engine.sweep("one", 1), range(4)))
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(self.engine.audit()["receipts"], 1)

    def test_receipt_tampering_is_detected(self):
        self.orders()
        self.engine.sweep("one", 1)
        with sqlite3.connect(self.path) as db:
            payload = json.loads(db.execute("SELECT payload FROM receipts").fetchone()[0])
            payload["executed_volume"] = 999
            db.execute("UPDATE receipts SET payload=?", (json.dumps(payload),))
        with self.assertRaisesRegex(ProtocolError, "integrity"):
            self.engine.audit()

    def test_ledger_tampering_is_detected(self):
        self.fund()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE balances SET available=available+1 WHERE owner='buyer'")
        with self.assertRaisesRegex(ProtocolError, "conservation"):
            self.engine.audit()

    def test_batch_cap_and_rejected_oversized_sweep(self):
        self.engine.fund("buyer", QUOTE, 1_000_000)
        for i in range(81):
            self.engine.submit("buyer", f"b{i}", "buy", 1, 1, 100)
        r = self.engine.sweep("one", 1)
        self.assertEqual(len(r["input_ids"]), 80)
        next_r = self.engine.sweep("next", 2)
        self.assertNotEqual(r["input_ids"], next_r["input_ids"])
        last = self.engine.state()["orders"][-1]["id"]
        self.assertIn(last, next_r["input_ids"])
        with self.assertRaises(ProtocolError):
            self.engine.sweep("two", 2, 81)

    def test_integer_overflow_and_wire_types_rejected(self):
        for value in (True, 1.5, "1", -1, 0, 2**63):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                self.engine.fund("a", QUOTE, value)
        with self.assertRaises(ProtocolError):
            self.engine.submit("a", "n", "buy", 2**62, 4, 10)
        with self.assertRaises(ProtocolError):
            fee(100, [])

    def test_fifty_percent_native_discount_at_exact_scale(self):
        self.assertEqual(fee(100_000, "foreign"), 50)
        self.assertEqual(fee(100_000, "native"), 25)

    def test_same_owner_opposite_side_is_rejected(self):
        self.fund()
        self.engine.fund("buyer", BASE, 100)
        self.engine.submit("buyer", "b", "buy", 1, 100, 100)
        with self.assertRaisesRegex(ProtocolError, "self-trade"):
            self.engine.submit("buyer", "s", "sell", 1, 100, 100)

    def test_seeded_random_operation_sequence(self):
        rng = random.Random(825)
        for owner in ("a", "b", "c", "d"):
            self.engine.fund(owner, QUOTE, 10_000_000)
            self.engine.fund(owner, BASE, 10000)
        for i in range(160):
            owner = rng.choice(["a", "b", "c", "d"])
            side = "buy" if owner in ("a", "b") else "sell"
            self.engine.submit(owner, str(i), side, rng.randint(1, 20), rng.randint(80, 120), 1000, rng.choice(["native", "foreign"]))
            if i % 7 == 0:
                self.engine.sweep(f"r{i}", i)
                self.engine.audit()
            if i % 11 == 0:
                open_orders = [o for o in self.engine.state()["orders"] if o["status"] == "open"]
                if open_orders:
                    o = rng.choice(open_orders)
                    self.engine.cancel(o["owner"], o["id"])
        self.engine.sweep("expire-all", 1000)
        self.engine.audit()
        self.assertTrue(all(b["locked"] == 0 for b in self.engine.state()["balances"]))


class AuctionTests(unittest.TestCase):
    def test_permutation_invariant_for_fixed_batch(self):
        orders = [dict(id="b1", side="buy", limit_price=110, remaining=5),
                  dict(id="b2", side="buy", limit_price=100, remaining=5),
                  dict(id="s1", side="sell", limit_price=90, remaining=8)]
        expected = clear(orders)
        rng = random.Random(42)
        for _ in range(100):
            rng.shuffle(orders)
            self.assertEqual(clear(orders), expected)

    def test_deterministic_tie_priority(self):
        orders = [dict(id="b", side="buy", limit_price=100, remaining=5),
                  dict(id="a", side="buy", limit_price=100, remaining=5),
                  dict(id="s", side="sell", limit_price=100, remaining=5)]
        self.assertEqual(clear(orders).fills, {"a": 5, "s": 5})

    def test_duplicate_input_rejected(self):
        order = dict(id="a", side="buy", limit_price=100, remaining=1)
        with self.assertRaises(ProtocolError):
            clear([order, order])

    def test_random_books_respect_limits_and_maximize_volume(self):
        rng = random.Random(343)
        for run in range(300):
            orders = [dict(id=str(i), side=rng.choice(["buy", "sell"]), limit_price=rng.randint(1, 20), remaining=rng.randint(1, 20)) for i in range(rng.randint(1, 80))]
            result = clear(orders)
            expected = max(min(sum(o["remaining"] for o in orders if o["side"] == "buy" and o["limit_price"] >= p), sum(o["remaining"] for o in orders if o["side"] == "sell" and o["limit_price"] <= p)) for p in range(1, 21))
            self.assertEqual(result.volume, expected)
            for side in ("buy", "sell"):
                self.assertEqual(sum(result.fills.get(o["id"], 0) for o in orders if o["side"] == side), result.volume)
            for order in orders:
                filled = result.fills.get(order["id"], 0)
                self.assertLessEqual(filled, order["remaining"])
                if filled:
                    self.assertTrue(result.price <= order["limit_price"] if order["side"] == "buy" else result.price >= order["limit_price"])
