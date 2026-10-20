from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from static_protocol.engine import Engine
from static_protocol.perps import PerpetualEngine, RiskConfig
from static_protocol.types import ProtocolError, QUOTE


class PerpetualTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "perps.sqlite"
        self.engine = PerpetualEngine(self.path)
        self.engine.fund_account("fund-l", "long", 1000000, 0)
        self.engine.fund_account("fund-s", "short", 1000000, 0)
        self.engine.publish_oracle("oracle-0", 10000, 10000, 0, 0)

    def open(self, **kwargs):
        return self.engine.open_pair("open", "long", "short", 10,
                                     kwargs.pop("long_margin", 20000),
                                     kwargs.pop("short_margin", 20000), 0, **kwargs)["result"]["pair_id"]

    def price(self, price, now=1):
        return self.engine.publish_oracle(f"oracle-{now}", price, price, now, now)

    def cash(self, owner):
        return next(a["cash"] for a in self.engine.state()["accounts"] if a["owner"] == owner)

    def funding_ready(self, now=3600):
        self.price(10000, now)

    def test_open_reserves_collateral_and_charges_fees(self):
        pair = self.open(long_rail="native")
        self.assertEqual(self.cash("long"), 979975)
        self.assertEqual(self.cash("short"), 979950)
        risk = self.engine.risk(pair, 0)
        self.assertEqual((risk["long_equity"], risk["short_equity"]), (20000, 20000))
        self.assertEqual(risk["maintenance_each"], 10000)
        self.assertEqual(self.engine.audit()["held_collateral"], 2000000)

    def test_profitable_close_pays_from_counterparty(self):
        pair = self.open()
        self.price(10500)
        result = self.engine.close_pair("close", pair, "long", 1)["result"]
        self.assertEqual(result["long_unrealized_pnl"], 5000)
        self.assertEqual(result["short_unrealized_pnl"], -5000)
        self.assertEqual((result["long_payout"], result["short_payout"]), (24947, 14947))
        self.assertEqual(self.cash("long"), 1004897)
        self.assertEqual(self.cash("short"), 994897)
        self.assertEqual(self.engine.state()["metadata"]["fees"], 206)
        self.engine.audit()

    def test_short_profit_is_symmetric(self):
        pair = self.open()
        self.price(9500)
        r = self.engine.close_pair("close", pair, "short", 1)["result"]
        self.assertEqual(r["short_unrealized_pnl"], 5000)
        self.assertEqual(r["short_payout"], 25000 - 48)
        self.engine.audit()

    def test_under_margin_rejected_without_debiting_other_side(self):
        before = self.engine.state()
        with self.assertRaisesRegex(ProtocolError, "initial margin"):
            self.engine.open_pair("bad", "long", "short", 10, 19999, 20000, 1)
        self.assertEqual(before, self.engine.state())

    def test_failed_counterparty_debit_rolls_back_first_debit(self):
        before = self.engine.state()
        with self.assertRaises(ProtocolError):
            self.engine.open_pair("bad", "long", "unfunded", 10, 20000, 20000, 1)
        self.assertEqual(before, self.engine.state())

    def test_self_pair_and_wrong_owner_rejected(self):
        with self.assertRaises(ProtocolError):
            self.engine.open_pair("self", "long", "long", 10, 20000, 20000, 0)
        pair = self.open()
        for fn in (lambda: self.engine.close_pair("wrong-close", pair, "other", 0),
                   lambda: self.engine.top_up("wrong-topup", pair, "other", 1, 0)):
            before = self.engine.state()
            with self.assertRaises(ProtocolError):
                fn()
            self.assertEqual(before, self.engine.state())

    def test_topup_moves_only_free_cash_and_can_rescue_margin(self):
        pair = self.open()
        self.price(11100)
        self.assertTrue(self.engine.risk(pair, 1)["short_liquidatable"])
        self.engine.top_up("topup", pair, "short", 5000, 1)
        self.assertFalse(self.engine.risk(pair, 1)["short_liquidatable"])
        with self.assertRaises(ProtocolError):
            self.engine.top_up("too-much", pair, "short", 1000000, 1)
        self.engine.audit()

    def test_withdraw_cannot_take_escrow_and_does_not_need_oracle(self):
        self.open()
        cash = self.cash("long")
        with self.assertRaises(ProtocolError):
            self.engine.fund_account("too-much", "long", cash + 1, 100, "out")
        self.engine.fund_account("withdraw", "long", cash, 100, "out")
        self.assertEqual(self.cash("long"), 0)
        self.engine.audit()

    def test_topup_allowed_during_oracle_staleness(self):
        pair = self.open()
        self.engine.top_up("topup", pair, "long", 1000, 100)
        self.engine.audit()

    def test_missing_stale_future_and_divergent_oracles_rejected(self):
        empty = PerpetualEngine(Path(self.temp.name) / "empty.sqlite")
        with self.assertRaisesRegex(ProtocolError, "oracle"):
            empty.open_pair("open", "a", "b", 1, 20, 20, 0)
        pair = self.open()
        for fn in (lambda: self.engine.close_pair("stale", pair, "long", 61),
                   lambda: self.engine.publish_oracle("future", 100, 100, 2, 1),
                   lambda: self.engine.publish_oracle("old", 100, 100, 0, 61),
                   lambda: self.engine.publish_oracle("divergent", 100, 106, 1, 1)):
            before = self.engine.state()
            with self.assertRaises(ProtocolError):
                fn()
            self.assertEqual(before, self.engine.state())

    def test_oracle_freshness_boundary(self):
        pair = self.open()
        self.engine.risk(pair, 60)
        with self.assertRaises(ProtocolError):
            self.engine.risk(pair, 61)

    def test_oracle_and_simulation_time_cannot_rewind(self):
        self.price(10000, 10)
        with self.assertRaises(ProtocolError):
            self.engine.fund_account("rewind", "long", 1, 9)
        with self.assertRaises(ProtocolError):
            self.engine.publish_oracle("old-observation", 10000, 10000, 9, 10)

    def test_positive_funding_long_pays_short_without_protocol_income(self):
        pair = self.open()
        self.funding_ready()
        fees = self.engine.state()["metadata"]["fees"]
        r = self.engine.settle_funding("funding", 1, 10, 3600)["result"]
        self.assertEqual(r["payments"], [{"pair_id": pair, "long_paid": 100, "short_paid": -100}])
        risk = self.engine.risk(pair, 3600)
        self.assertEqual((risk["long_equity"], risk["short_equity"]), (19900, 20100))
        self.assertEqual(self.engine.state()["metadata"]["fees"], fees)
        self.engine.audit()

    def test_negative_funding_short_pays_long(self):
        pair = self.open()
        self.funding_ready()
        self.engine.settle_funding("funding", 1, -10, 3600)
        self.assertEqual(self.engine.risk(pair, 3600)["long_equity"], 20100)

    def test_funding_uses_index_not_mark(self):
        pair = self.open()
        self.engine.publish_oracle("index", 10000, 10100, 3600, 3600)
        r = self.engine.settle_funding("funding", 1, 100, 3600)["result"]
        self.assertEqual(r["payments"][0]["long_paid"], 1000)
        self.assertEqual(self.engine.risk(pair, 3600)["long_equity"], 20000)

    def test_funding_dust_rounds_toward_zero_symmetrically(self):
        small = PerpetualEngine(Path(self.temp.name) / "small.sqlite")
        for i, owner in enumerate(("a", "b")):
            small.fund_account(f"d{i}", owner, 100, 0)
        small.publish_oracle("o", 1, 1, 0, 0)
        small.open_pair("open", "a", "b", 1, 1, 1, 0)
        small.publish_oracle("o2", 1, 1, 3600, 3600)
        r = small.settle_funding("f", 1, -1, 3600)["result"]
        self.assertEqual(r["payments"][0]["long_paid"], 0)
        self.assertEqual(r["payments"][0]["short_paid"], 0)

    def test_funding_timing_caps_and_duplicate_epochs(self):
        self.open()
        with self.assertRaises(ProtocolError):
            self.engine.settle_funding("early", 1, 10, 3599)
        self.funding_ready()
        for epoch, rate in ((2, 10), (1, 101), (1, -101)):
            with self.assertRaises(ProtocolError):
                self.engine.settle_funding("bad", epoch, rate, 3600)
        first = self.engine.settle_funding("funding", 1, 10, 3600)
        self.assertEqual(first, self.engine.settle_funding("funding", 1, 10, 3600))
        with self.assertRaises(ProtocolError):
            self.engine.settle_funding("duplicate", 1, 10, 3600)
        with self.assertRaises(ProtocolError):
            self.engine.settle_funding("fast", 2, 10, 3601)

    def test_funding_rejects_liquidatable_pair_without_partial_accrual(self):
        self.open()
        self.engine.open_pair("open2", "long", "short", 1, 50000, 50000, 0)
        self.price(12000, 3600)
        before = self.engine.state()
        with self.assertRaisesRegex(ProtocolError, "liquidatable"):
            self.engine.settle_funding("funding", 1, 10, 3600)
        self.assertEqual(before, self.engine.state())

    def test_healthy_pair_cannot_be_liquidated(self):
        pair = self.open()
        before = self.engine.state()
        with self.assertRaises(ProtocolError):
            self.engine.liquidate("liq", pair, 0)
        self.assertEqual(before, self.engine.state())

    def test_maintenance_equality_is_not_liquidatable(self):
        pair = self.open()
        for epoch in range(1, 11):
            now = epoch * 3600
            self.funding_ready(now)
            self.engine.settle_funding(f"funding-{epoch}", epoch, 100, now)
        self.assertEqual(self.engine.risk(pair, 36000)["long_equity"], 10000)
        self.assertFalse(self.engine.risk(pair, 36000)["long_liquidatable"])
        with self.assertRaises(ProtocolError):
            self.engine.liquidate("liq", pair, 36000)

    def test_liquidation_below_maintenance_conserves_and_closes_pair(self):
        pair = self.open()
        self.price(11100)
        r = self.engine.liquidate("liq", pair, 1)["result"]
        self.assertTrue(r["short_liquidatable"])
        self.assertEqual(r["status"], "liquidated")
        self.assertEqual(r["insurance_used"], 0)
        self.assertEqual(r["uncovered_loss"], 0)
        self.assertEqual(self.engine.state()["pairs"][0]["status"], "liquidated")
        self.engine.audit()

    def test_gap_loss_uses_insurance_and_discloses_haircut(self):
        pair = self.open()
        self.engine.fund_insurance("insurance", 10000, 0)
        self.price(15000)
        r = self.engine.liquidate("liq", pair, 1)["result"]
        self.assertEqual(r["deficit"], 30000)
        self.assertEqual(r["insurance_used"], 10000)
        self.assertEqual(r["uncovered_loss"], 20000)
        self.assertEqual(r["long_haircut"], 20000)
        self.assertEqual(r["short_payout"], 0)
        self.assertEqual(r["long_payout"], 50000 - 75)
        self.assertEqual(self.cash("short"), 979950)  # Unrelated free cash is isolated.
        self.assertEqual(self.engine.audit()["uncovered_loss"], 20000)

    def test_full_insurance_avoids_haircut(self):
        pair = self.open()
        self.engine.fund_insurance("insurance", 40000, 0)
        self.price(15000)
        r = self.engine.liquidate("liq", pair, 1)["result"]
        self.assertEqual(r["long_haircut"], 0)
        self.assertEqual(r["long_payout"], 70000 - 75)
        self.assertEqual(self.engine.state()["metadata"]["insurance"], 10000)
        self.engine.audit()

    def test_long_bankruptcy_is_symmetric(self):
        pair = self.open()
        self.engine.fund_insurance("insurance", 5000, 0)
        self.price(5000)
        r = self.engine.close_pair("close", pair, "short", 1)["result"]
        self.assertEqual(r["long_payout"], 0)
        self.assertEqual(r["short_haircut"], 25000)
        self.assertEqual(r["short_payout"], 45000 - 25)
        self.engine.audit()

    def test_all_mutations_retry_safely_and_conflicting_payload_rejected(self):
        first = self.engine.fund_account("extra", "long", 50, 0)
        self.assertEqual(first, self.engine.fund_account("extra", "long", 50, 0))
        with self.assertRaises(ProtocolError):
            self.engine.fund_account("extra", "long", 51, 0)
        pair = self.open()
        before = self.engine.state()
        opened = self.engine.open_pair("open", "long", "short", 10, 20000, 20000, 0)
        self.assertEqual(opened["result"]["pair_id"], pair)
        self.assertEqual(before, self.engine.state())
        self.engine.close_pair("close", pair, "long", 0)
        count = self.engine.audit()["receipts"]
        self.engine.close_pair("close", pair, "long", 0)
        self.assertEqual(self.engine.audit()["receipts"], count)
        with self.assertRaises(ProtocolError):
            self.engine.close_pair("other-close", pair, "long", 0)

    def test_close_after_funding_includes_accrual_once(self):
        pair = self.open()
        self.funding_ready()
        self.engine.settle_funding("funding", 1, 10, 3600)
        result = self.engine.close_pair("close", pair, "long", 3600)["result"]
        self.assertEqual(result["long_payout"], 19900 - 50)
        self.assertEqual(result["short_payout"], 20100 - 50)
        self.engine.audit()

    def test_concurrent_open_retry_creates_only_one_pair(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.engine.open_pair("same", "long", "short", 10, 20000, 20000, 0), range(4)))
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(len(self.engine.state()["pairs"]), 1)
        self.engine.audit()

    def test_concurrent_close_cannot_spend_escrow_twice(self):
        pair = self.open()
        def close(i):
            try:
                return self.engine.close_pair(f"close-{i}", pair, "long", 0)
            except ProtocolError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(close, range(2)))
        self.assertEqual(sum(r is not None for r in result), 1)
        self.engine.audit()

    def test_fault_mid_close_rolls_back_every_balance(self):
        pair = self.open()
        before = self.engine.state()
        with patch.object(self.engine, "_assert_invariants", side_effect=RuntimeError("injected fault")):
            with self.assertRaises(RuntimeError):
                self.engine.close_pair("close", pair, "long", 0)
        self.assertEqual(before, self.engine.state())
        self.engine.close_pair("close", pair, "long", 0)
        self.engine.audit()

    def test_restart_preserves_pairs_and_funding(self):
        self.open()
        self.funding_ready()
        self.engine.settle_funding("funding", 1, -10, 3600)
        restored = PerpetualEngine(self.path)
        self.assertEqual(restored.state(), self.engine.state())
        self.assertEqual(restored.audit(), self.engine.audit())

    def test_configuration_cannot_change_silently_on_restart(self):
        with self.assertRaisesRegex(ProtocolError, "configuration"):
            PerpetualEngine(self.path, RiskConfig(initial_margin_bps=3000))

    def test_tampering_with_ledger_and_receipts_is_detected(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE perp_accounts SET cash=cash+1 WHERE owner='long'")
        with self.assertRaisesRegex(ProtocolError, "conservation"):
            self.engine.audit()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE perp_accounts SET cash=cash-1 WHERE owner='long'")
            db.execute("UPDATE perp_receipts SET hash='bad' WHERE seq=1")
        with self.assertRaisesRegex(ProtocolError, "receipt"):
            self.engine.audit()

    def test_spot_and_perpetual_ledgers_remain_separate(self):
        spot = Engine(self.path)
        spot.fund("long", QUOTE, 1234)
        self.open()
        self.assertEqual(spot.state()["balances"][0]["available"], 1234)
        spot.audit()
        self.engine.audit()

    def test_state_commitment_detects_value_conserving_position_tampering(self):
        pair = self.open()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE perp_pairs SET funding_long=1 WHERE id=?", (pair,))
        with self.assertRaisesRegex(ProtocolError, "state commitment"):
            self.engine.audit()
        with self.assertRaisesRegex(ProtocolError, "state commitment"):
            self.engine.fund_account("hide-tamper", "long", 1, 0)

    def test_noninteger_and_overflow_inputs_are_rejected(self):
        for amount in (True, 1.1, "1", -1, 0, 2**63):
            with self.subTest(amount=amount), self.assertRaises(ProtocolError):
                self.engine.fund_account("bad", "long", amount, 0)
        with self.assertRaises(ProtocolError):
            self.engine.open_pair("large", "long", "short", 2**62, 20000, 20000, 0)
        pair = self.open()
        before = self.engine.state()
        with self.assertRaises(ProtocolError):
            self.engine.publish_oracle("large-price", 2**62, 2**62, 1, 1)
        self.assertEqual(before, self.engine.state())
        with self.assertRaises(ProtocolError):
            self.engine.risk({}, 0)

    def test_random_price_moves_close_without_creating_collateral(self):
        rng = random.Random(434)
        self.engine.fund_insurance("ins", 100000, 0)
        for i in range(60):
            now = i * 2 + 1
            self.price(10000, now)
            opened = self.engine.open_pair(f"open-{i}", "long", "short", rng.randint(1, 10), 20000, 20000, now)
            pair = opened["result"]["pair_id"]
            self.price(rng.randint(1000, 20000), now + 1)
            self.engine.close_pair(f"close-{i}", pair, rng.choice(["long", "short"]), now + 1)
            self.engine.audit()


class RiskConfigurationTests(unittest.TestCase):
    def test_invalid_risk_parameters_rejected(self):
        for kwargs in ({"initial_margin_bps": 0}, {"initial_margin_bps": 10001},
                       {"maintenance_margin_bps": 2000}, {"oracle_max_age": 0},
                       {"funding_interval": 0}, {"funding_cap_bps": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ProtocolError):
                RiskConfig(**kwargs)
