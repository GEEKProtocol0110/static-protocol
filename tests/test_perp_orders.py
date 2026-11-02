from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from static_protocol.perps import PerpetualEngine, RiskConfig
from static_protocol.types import ProtocolError


class OrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "orders.sqlite"
        self.engine = PerpetualEngine(self.path)
        for owner in ("a", "b", "c"):
            self.engine.fund_account("deposit-"+owner, owner, 1000000, 0)
        self.engine.publish_oracle("oracle", 10000, 10000, 0, 0)

    def order(self, owner="a", side="long", quantity=10, price=10000, margin=3000,
              nonce="n", now=0, expiry=10000, rail="foreign"):
        return self.engine.place_order("order-"+owner+"-"+nonce, owner, nonce, side,
                                       quantity, price, margin, expiry, now, rail)["result"]

    def pair(self):
        self.order()
        self.order("b", "short")
        return self.engine.match_orders("match", 0)["result"]["matches"][0]["pair_id"]

    def cash(self, owner):
        return next(a["cash"] for a in self.engine.state()["accounts"] if a["owner"] == owner)

    def test_collateral_and_worst_case_fee_are_locked_before_matching(self):
        order = self.order(quantity=3, price=10100, margin=3000)
        self.assertEqual(order["reserved"], 3*(3000+6))
        self.assertEqual(self.cash("a"), 1000000-order["reserved"])
        self.assertEqual(self.engine.state()["metadata"]["fees"], 0)
        with self.assertRaises(ProtocolError):
            self.engine.fund_account("overdraw", "a", self.cash("a")+1, 0, "out")
        self.engine.audit()

    def test_limit_orders_match_and_create_real_ledger_positions(self):
        long = self.order(quantity=3, price=10100)
        short = self.order("b", "short", quantity=3, price=9900)
        result = self.engine.match_orders("m", 0)["result"]
        self.assertEqual(result["matched_quantity"], 3)
        self.assertEqual(len(result["matches"]), 1)
        match = result["matches"][0]
        self.assertEqual(match["entry_price"], 10000)
        self.assertEqual(match["long_order"], long["id"])
        self.assertEqual(match["short_order"], short["id"])
        self.assertEqual((match["long_margin"], match["short_margin"]), (9000, 9000))
        self.assertEqual(self.cash("a"), 1000000-9000-15)
        self.assertEqual(self.cash("b"), 1000000-9000-15)
        self.assertEqual(self.engine.risk(match["pair_id"], 0)["long_equity"], 9000)
        self.assertTrue(all(o["reserved"]==0 for o in self.engine.state()["orders"]))
        self.engine.audit()

    def test_partial_fill_keeps_remaining_reservation(self):
        long = self.order(quantity=10)
        self.order("b", "short", quantity=3)
        self.engine.match_orders("m", 0)
        residual = next(o for o in self.engine.state()["orders"] if o["id"]==long["id"])
        self.assertEqual((residual["remaining"], residual["status"]), (7, "open"))
        self.assertEqual(residual["reserved"], 7*(3000+5))
        self.engine.cancel_order("cancel", long["id"], "a", 0)
        self.assertEqual(self.cash("a"), 1000000-9000-15)
        self.engine.audit()

    def test_price_and_time_priority(self):
        first = self.order("a", quantity=1, price=10100, nonce="early")
        later = self.order("c", quantity=1, price=10100, nonce="late")
        better = self.order("a", quantity=1, price=10200, nonce="better")
        self.order("b", "short", quantity=2)
        result = self.engine.match_orders("m", 0)["result"]["matches"]
        self.assertEqual([m["long_order"] for m in result], [better["id"], first["id"]])
        self.assertNotIn(later["id"], [m["long_order"] for m in result])

    def test_multiple_counterparties_split_one_order(self):
        self.order(quantity=5)
        self.order("b", "short", quantity=2)
        self.order("c", "short", quantity=3)
        result = self.engine.match_orders("m", 0)["result"]
        self.assertEqual([m["quantity"] for m in result["matches"]], [2, 3])
        self.assertEqual(len(set(m["pair_id"] for m in result["matches"])), 2)
        self.engine.audit()

    def test_crossing_limits_do_not_override_the_mock_mark(self):
        self.order(price=9900)
        self.order("b", "short", price=9800)
        result = self.engine.match_orders("m", 0)["result"]
        self.assertEqual(result["matched_quantity"], 0)
        self.assertEqual(self.engine.state()["pairs"], [])

    def test_short_margin_budget_caps_execution_price(self):
        self.order(price=10500, margin=3000)
        short = self.order("b", "short", price=10000, margin=2000)
        self.assertEqual(short["price_cap"], 10000)
        self.engine.publish_oracle("o2", 10100, 10100, 1, 1)
        result = self.engine.match_orders("m", 1)["result"]
        self.assertEqual(result["matched_quantity"], 0)
        self.engine.audit()

    def test_expiry_boundary_refunds_order_before_matching(self):
        self.order(expiry=5)
        self.order("b", "short", expiry=5)
        result = self.engine.match_orders("m", 5)["result"]
        self.assertEqual(len(result["expired_ids"]), 2)
        self.assertEqual(result["matched_quantity"], 0)
        self.assertEqual(self.cash("a"), 1000000)
        self.assertEqual(self.cash("b"), 1000000)

    def test_cancellation_works_with_stale_oracle_and_wrong_owner_cannot_cancel(self):
        order = self.order()
        with self.assertRaises(ProtocolError):
            self.engine.cancel_order("bad", order["id"], "b", 0)
        self.engine.cancel_order("cancel", order["id"], "a", 100)
        self.assertEqual(self.cash("a"), 1000000)
        self.engine.audit()

    def test_filled_order_cannot_be_cancelled(self):
        self.pair()
        order = self.engine.state()["orders"][0]
        with self.assertRaises(ProtocolError):
            self.engine.cancel_order("cancel", order["id"], order["owner"], 0)

    def test_order_nonce_and_request_retries_do_not_relock_funds(self):
        original = self.order()
        cash = self.cash("a")
        self.assertEqual(original, self.order())
        retry = self.engine.place_order("new-request", "a", "n", "long", 10, 10000, 3000, 10000, 1)["result"]
        self.assertEqual(original, retry)
        self.assertEqual(self.cash("a"), cash)
        with self.assertRaisesRegex(ProtocolError, "nonce"):
            self.engine.place_order("changed", "a", "n", "long", 11, 10000, 3000, 10000, 1)
        self.engine.cancel_order("cancel", original["id"], "a", 1)
        with self.assertRaises(ProtocolError):
            self.engine.place_order("changed-again", "a", "n", "long", 11, 10000, 3000, 10000, 1)

    def test_self_trade_intents_rejected_but_expired_opposite_is_not_a_block(self):
        self.order(expiry=5)
        with self.assertRaisesRegex(ProtocolError, "self-trade"):
            self.order(side="short", nonce="opposite")
        self.order(side="short", nonce="after-expiry", now=5)
        self.engine.match_orders("expire", 5)
        self.assertEqual(self.engine.state()["orders"][0]["status"], "expired")
        self.engine.audit()

    def test_order_and_matching_input_validation(self):
        for kwargs in (dict(quantity=True), dict(price=0), dict(margin=1), dict(expiry=0),
                       dict(rail=[]), dict(side="buy")):
            with self.subTest(kwargs=kwargs), self.assertRaises(ProtocolError):
                self.order(**kwargs)
        with self.assertRaises(ProtocolError):
            self.engine.match_orders("big", 0, 81)
        with self.assertRaises(ProtocolError):
            self.engine.match_orders("stale", 61)

    def test_insufficient_cash_rolls_back_placement(self):
        before = self.engine.state()
        with self.assertRaises(ProtocolError):
            self.order(quantity=1000)
        self.assertEqual(self.engine.state(), before)

    def test_rotating_admission_reaches_order_beyond_first_eighty(self):
        for i in range(80):
            self.order(quantity=1, nonce=str(i))
        last = self.order("b", "short", quantity=1)
        self.assertEqual(self.engine.match_orders("first", 0)["result"]["matched_quantity"], 0)
        second = self.engine.match_orders("second", 0)["result"]
        self.assertEqual(second["matched_quantity"], 1)
        self.assertIn(last["id"], second["input_ids"])

    def test_repeated_small_partial_fills_are_fully_fee_reserved(self):
        self.engine.publish_oracle("small", 1, 1, 0, 0)
        self.order(quantity=10, price=1, margin=1)
        for i in range(10):
            self.order("b", "short", quantity=1, price=1, margin=1, nonce=str(i))
            self.engine.match_orders(f"m{i}", 0)
        self.assertEqual(self.cash("a"), 1000000-20)
        self.assertEqual(self.engine.state()["metadata"]["fees"], 20)
        self.engine.audit()

    def test_concurrent_matching_retry_creates_no_duplicate_pairs(self):
        self.order()
        self.order("b", "short")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.engine.match_orders("m", 0), range(4)))
        self.assertTrue(all(r==results[0] for r in results))
        self.assertEqual(len(self.engine.state()["pairs"]), 1)
        self.engine.audit()

    def test_concurrent_order_intake_cannot_reserve_the_same_cash_twice(self):
        def place(i):
            try:
                return self.order(quantity=200, nonce=f"race-{i}")
            except ProtocolError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(place,range(2)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.engine.audit()

    def test_mid_match_failure_rolls_back_positions_orders_cash_and_receipt(self):
        self.order()
        self.order("b", "short")
        before=self.engine.state()
        with patch.object(self.engine, "_assert_invariants", side_effect=RuntimeError("fault")):
            with self.assertRaises(RuntimeError):
                self.engine.match_orders("m", 0)
        self.assertEqual(before, self.engine.state())
        self.assertEqual(self.engine.match_orders("m",0)["result"]["matched_quantity"], 10)

    def test_restart_keeps_order_reservations(self):
        self.order()
        self.assertEqual(self.engine.state(), PerpetualEngine(self.path).state())
        PerpetualEngine(self.path).audit()

    def test_limit_tampering_is_detected_even_without_balance_change(self):
        order=self.order()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE perp_orders SET limit_price=limit_price-1 WHERE id=?",(order["id"],))
        with self.assertRaisesRegex(ProtocolError,"commitment"):
            self.engine.audit()


class ReductionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine=PerpetualEngine(Path(self.temp.name)/"reduce.sqlite")
        self.engine.fund_account("a","a",1000000,0)
        self.engine.fund_account("b","b",1000000,0)
        self.engine.publish_oracle("o",10000,10000,0,0)
        self.pair=self.engine.open_pair("p","a","b",10,30003,30007,0)["result"]["pair_id"]

    def test_partial_close_realizes_only_closed_size_and_keeps_entry(self):
        self.engine.publish_oracle("o2",10500,10500,1,1)
        r=self.engine.reduce_pair("reduce",self.pair,"a",3,1)["result"]
        pair=self.engine.state()["pairs"][0]
        self.assertEqual((r["closed_quantity"],r["remaining_quantity"]),(3,7))
        self.assertEqual(r["long_realized_pnl"],1500)
        self.assertEqual((pair["quantity"],pair["entry_price"],pair["status"]),(7,10000,"open"))
        self.assertEqual((pair["long_margin"],pair["short_margin"]),(21003,21005))
        self.engine.audit()

    def test_repeated_dust_splits_preserve_all_margin_and_funding(self):
        self.engine.publish_oracle("o2",10051,10000,3600,3600)
        funding=self.engine.settle_funding("f",1,-100,3600)["result"]["payments"][0]["long_paid"]
        results=[]
        for i in range(10):
            results.append(self.engine.reduce_pair(f"r{i}",self.pair,"a",1,3600)["result"])
        self.assertEqual(sum(r["funding_long_paid"] for r in results),funding)
        self.assertEqual(sum(r["long_equity"] for r in results),30003-funding)
        self.assertEqual(sum(r["short_equity"] for r in results),30007+funding)
        self.assertEqual(results[-1]["status"],"closed")
        self.engine.audit()

    def test_funding_after_reduction_uses_remaining_quantity(self):
        self.engine.reduce_pair("r",self.pair,"b",4,0)
        self.engine.publish_oracle("o2",10000,10000,3600,3600)
        r=self.engine.settle_funding("f",1,10,3600)["result"]
        self.assertEqual(r["payments"][0]["long_paid"],60)

    def test_wrong_owner_invalid_quantity_and_stale_oracle_rejected(self):
        for owner,quantity,now in (("wrong",1,0),("a",0,0),("a",11,0),("a",True,0),("a",1,61)):
            before=self.engine.state()
            with self.assertRaises(ProtocolError):
                self.engine.reduce_pair("bad",self.pair,owner,quantity,now)
            self.assertEqual(before,self.engine.state())

    def test_reduction_does_not_extract_collateral_from_liquidatable_pair(self):
        self.engine.publish_oracle("o2",13000,13000,1,1)
        before=self.engine.state()
        with self.assertRaisesRegex(ProtocolError,"liquidatable"):
            self.engine.reduce_pair("r",self.pair,"a",1,1)
        self.assertEqual(before,self.engine.state())
        self.engine.liquidate("l",self.pair,1)
        self.engine.audit()

    def test_full_reduction_uses_existing_insurance_and_haircut_policy(self):
        self.engine.publish_oracle("o2",15000,15000,1,1)
        r=self.engine.reduce_pair("r",self.pair,"b",10,1)["result"]
        self.assertEqual(r["status"],"closed")
        self.assertGreater(r["uncovered_loss"],0)
        self.engine.audit()

    def test_same_reduction_request_never_reduces_twice(self):
        first=self.engine.reduce_pair("r",self.pair,"a",3,0)
        self.assertEqual(first,self.engine.reduce_pair("r",self.pair,"a",3,0))
        self.assertEqual(self.engine.state()["pairs"][0]["quantity"],7)

    def test_reduction_fault_rolls_back_payout_and_residual(self):
        before=self.engine.state()
        with patch.object(self.engine,"_assert_invariants",side_effect=RuntimeError("fault")):
            with self.assertRaises(RuntimeError):
                self.engine.reduce_pair("r",self.pair,"a",3,0)
        self.assertEqual(before,self.engine.state())

    def test_dust_reductions_cannot_create_negative_slice_or_unhealthy_residual(self):
        cases=[(2,-3334,1,"negative slice"),(1,6667,1,"residual")]
        for i,(entry,rate,exit_price,error) in enumerate(cases):
            with self.subTest(case=i):
                e=PerpetualEngine(Path(self.temp.name)/f"dust-{i}.sqlite",RiskConfig(funding_cap_bps=10000))
                e.fund_account("a","a",100,0)
                e.fund_account("b","b",100,0)
                e.publish_oracle("o",entry,entry,0,0)
                margin=2 if entry==2 else 3
                pair=e.open_pair("p","a","b",3,margin,10,0)["result"]["pair_id"]
                e.publish_oracle("o2",entry,entry,3600,3600)
                e.settle_funding("f",1,rate,3600)
                e.publish_oracle("o3",exit_price,exit_price,3601,3601)
                self.assertFalse(e.risk(pair,3601)["long_liquidatable"])
                before=e.state()
                with self.assertRaisesRegex(ProtocolError,error):
                    e.reduce_pair("r",pair,"a",1,3601)
                self.assertEqual(before,e.state())
                e.close_pair("close",pair,"a",3601)
                e.audit()

    def test_seeded_partitions_preserve_gross_equity_and_funding(self):
        self.engine.close_pair("initial-close",self.pair,"a",0)
        rng=random.Random(903)
        for i in range(20):
            start=i*7200+1
            self.engine.publish_oracle(f"o{i}",10000,10000,start,start)
            q=rng.randint(2,15)
            ml=q*5000+rng.randint(0,9)
            ms=q*5000+rng.randint(0,9)
            pair=self.engine.open_pair(f"p{i}","a","b",q,ml,ms,start)["result"]["pair_id"]
            self.engine.publish_oracle(f"f-o{i}",10051,10000,start+3600,start+3600)
            rate=rng.choice([-100,-33,33,100])
            f=self.engine.settle_funding(f"f{i}",i+1,rate,start+3600)["result"]["payments"][0]["long_paid"]
            price=rng.randint(9950,10050)
            self.engine.publish_oracle(f"exit-o{i}",price,price,start+3601,start+3601)
            left=q
            results=[]
            while left:
                size=rng.randint(1,left)
                r=self.engine.reduce_pair(f"r{i}-{left}",pair,"a",size,start+3601)["result"]
                results.append(r)
                left-=size
            self.assertEqual(sum(r["long_realized_pnl"] for r in results),q*(price-10000))
            self.assertEqual(sum(r["funding_long_paid"] for r in results),f)
            self.assertEqual(sum(r["long_payout"]+r["exit_fees"]["long"] for r in results),ml+q*(price-10000)-f)
            self.assertEqual(sum(r["short_payout"]+r["exit_fees"]["short"] for r in results),ms-q*(price-10000)+f)
            self.engine.audit()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"legacy.sqlite"
        fixture=Path(__file__).parent/"fixtures/perps-v0.2.sql"
        with sqlite3.connect(self.path) as db:
            db.executescript(fixture.read_text())

    def test_real_v02_fixture_migrates_preserving_receipt_prefix_and_collateral(self):
        with sqlite3.connect(self.path) as db:
            prior=list(db.execute("SELECT payload,hash FROM perp_receipts ORDER BY seq"))
        e=PerpetualEngine(self.path)
        self.assertEqual(e.state()["metadata"]["schema"],2)
        self.assertEqual(e.audit()["held_collateral"],2005000)
        receipts=e.state()["receipts"]
        self.assertEqual(receipts[-1]["kind"],"schema_migration")
        self.assertEqual(receipts[-1]["previous_hash"],"766977f050ab1bcad3dd9f05d8f0ed1ead2412388240fc25bae11874ecf05896")
        with sqlite3.connect(self.path) as db:
            self.assertEqual(prior,list(db.execute("SELECT payload,hash FROM perp_receipts ORDER BY seq LIMIT 8")))
        pair=e.state()["pairs"][0]["id"]
        r=e.reduce_pair("partial",pair,"alice",1,3600)["result"]
        self.assertEqual((r["remaining_quantity"],r["funding_long_paid"]),(2,100))
        e.audit()

    def test_migration_is_idempotent_and_legacy_retry_still_works(self):
        e=PerpetualEngine(self.path)
        before=e.state()
        self.assertEqual(before,PerpetualEngine(self.path).state())
        old=e.fund_account("legacy-a","alice",1000000,0)
        self.assertEqual(old["kind"],"account_funding")
        self.assertEqual(before,e.state())

    def test_corrupt_legacy_state_is_not_migrated(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE perp_pairs SET funding_long=funding_long+1")
        with self.assertRaisesRegex(ProtocolError,"integrity"):
            PerpetualEngine(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT value FROM perp_meta WHERE key='schema'").fetchone()[0],1)

    def test_corrupt_legacy_receipt_chain_is_not_migrated(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE perp_receipts SET hash='bad' WHERE seq=1")
        with self.assertRaisesRegex(ProtocolError,"receipt chain"):
            PerpetualEngine(self.path)
