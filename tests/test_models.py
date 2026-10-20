import random
import unittest

from static_protocol.adapters import LiveSettlementAdapter
from static_protocol.reserves import LinearReserve, ConstantProductPool
from static_protocol.treasury import Treasury, EPOCH_SECONDS
from static_protocol.types import ProtocolError


class CurveTests(unittest.TestCase):
    def test_buy_burn_roundtrip_does_not_create_value(self):
        curve = LinearReserve()
        paid = curve.buy(11, 2000)
        returned = curve.sell(11)
        self.assertLessEqual(returned, paid)
        self.assertEqual(curve.supply, 0)
        self.assertEqual(curve.reserve, paid - returned)

    def test_random_roundtrips_remain_fully_backed(self):
        rng = random.Random(3)
        curve = LinearReserve()
        paid = returned = 0
        for _ in range(1000):
            if curve.supply and rng.randrange(2):
                returned += curve.sell(rng.randint(1, curve.supply))
            else:
                paid += curve.buy(rng.randint(1, 100), 10_000_000)
            self.assertEqual(curve.reserve, paid - returned)
            curve.audit()
        returned += curve.sell(curve.supply) if curve.supply else 0
        self.assertLessEqual(returned, paid)

    def test_cap_and_slippage_fail_without_mutation(self):
        curve = LinearReserve()
        for lots, cap in ((5000, 100000000), (10, 1)):
            with self.assertRaises(ProtocolError):
                curve.buy(lots, cap)
            self.assertEqual((curve.supply, curve.reserve), (0, 0))
        curve.buy(10, 2000)
        before = (curve.supply, curve.reserve)
        with self.assertRaises(ProtocolError):
            curve.sell(10, 99999)
        self.assertEqual(before, (curve.supply, curve.reserve))

    def test_redemption_continues_below_threshold(self):
        curve = LinearReserve(graduation_threshold=10**9)
        curve.buy(10, 2000)
        self.assertFalse(curve.graduation_ready)
        self.assertGreater(curve.sell(5), 0)

    def test_threshold_only_signals_does_not_lock_redemption(self):
        curve = LinearReserve(graduation_threshold=100)
        curve.buy(10, 2000)
        self.assertTrue(curve.graduation_ready)
        self.assertGreater(curve.sell(10), 0)

    def test_cannot_restore_underbacked_state(self):
        with self.assertRaises(ProtocolError):
            LinearReserve(supply=10, reserve=1)


class AMMTests(unittest.TestCase):
    def test_product_never_decreases(self):
        pool = ConstantProductPool(1000000, 10000000)
        for i in range(100):
            before = pool.base_reserve * pool.quote_reserve
            pool.swap("base" if i % 2 else "quote", 1000)
            self.assertGreaterEqual(pool.base_reserve * pool.quote_reserve, before)

    def test_roundtrip_no_free_money(self):
        pool = ConstantProductPool(1000000, 10000000)
        output = pool.swap("base", 1000)
        returned = pool.swap("quote", output)
        self.assertLessEqual(returned, 1000)

    def test_slippage_atomicity(self):
        pool = ConstantProductPool(1000, 1000)
        with self.assertRaises(ProtocolError):
            pool.swap("base", 10, 100)
        self.assertEqual((pool.base_reserve, pool.quote_reserve), (1000, 1000))


class TreasuryTests(unittest.TestCase):
    def test_split_and_conservation(self):
        t = Treasury()
        self.assertEqual(t.allocate_margin(1_000_000), {"sovereign": 500000, "backstop": 250000, "opex": 250000})

    def test_tiny_margin_allocation_has_no_rounding_extraction(self):
        t = Treasury()
        for i in range(1000):
            split = t.allocate_margin(1)
            self.assertEqual(sum(split.values()), 1)
            self.assertTrue(all(v >= 0 for v in split.values()))
        self.assertEqual((t.sovereign, t.backstop, t.opex), (500, 250, 250))

    def test_linear_drip_and_epoch_reset_guard(self):
        t = Treasury()
        t.allocate_margin(1_000_000)
        t.begin_epoch(0)
        with self.assertRaises(ProtocolError):
            t.drip_opex(0, 1)
        self.assertEqual(t.drip_opex(EPOCH_SECONDS // 2, 6250), 6250)
        with self.assertRaises(ProtocolError):
            t.drip_opex(EPOCH_SECONDS // 2, 1)
        with self.assertRaises(ProtocolError):
            t.begin_epoch(EPOCH_SECONDS - 1)
        t.drip_opex(EPOCH_SECONDS, 6250)
        with self.assertRaises(ProtocolError):
            t.drip_opex(EPOCH_SECONDS, 1)
        t.begin_epoch(EPOCH_SECONDS)
        with self.assertRaises(ProtocolError):
            t.drip_opex(EPOCH_SECONDS, 1)

    def test_new_margin_does_not_expand_current_epoch_spending(self):
        t = Treasury()
        t.allocate_margin(400)
        t.begin_epoch(0)
        t.allocate_margin(40000)
        with self.assertRaises(ProtocolError):
            t.drip_opex(EPOCH_SECONDS, 6)
        t.drip_opex(EPOCH_SECONDS, 5)

    def test_sovereign_unlock_cannot_be_faked_with_caller_claim(self):
        t = Treasury()
        t.allocate_margin(1_000_000)
        with self.assertRaises(ProtocolError):
            t.release_sovereign(proof={"insolvent": True})
        self.assertEqual(t.sovereign, 500000)

    def test_nonmonotonic_time_rejected(self):
        t = Treasury()
        t.allocate_margin(1000000)
        t.begin_epoch(10)
        with self.assertRaises(ProtocolError):
            t.drip_opex(9, 1)


class AdapterTests(unittest.TestCase):
    def test_all_live_paths_fail_closed(self):
        adapter = LiveSettlementAdapter()
        for fn in (lambda: adapter.broadcast({}), adapter.convert_foreign_collateral,
                   adapter.convert_protocol_fees_to_kas, adapter.graduate_curve):
            with self.assertRaises(ProtocolError):
                fn()
