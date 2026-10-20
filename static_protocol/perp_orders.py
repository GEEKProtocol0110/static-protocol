"""Collateralized limit-order intake and mock-mark matching for perpetuals.

This is a price-limit gate around a simulated mark, not price discovery or a
live exchange. The mixin uses PerpetualEngine's atomic ledger and receipts.
"""

from .types import MAX_INT, ProtocolError, digest, fee, identifier, integer

ORDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS perp_orders(
 seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL, nonce TEXT NOT NULL,
 side TEXT NOT NULL CHECK(side IN ('long','short')), quantity INTEGER NOT NULL CHECK(quantity>0),
 remaining INTEGER NOT NULL CHECK(remaining>=0), limit_price INTEGER NOT NULL CHECK(limit_price>0),
 margin_per_lot INTEGER NOT NULL CHECK(margin_per_lot>0), price_cap INTEGER NOT NULL CHECK(price_cap>0),
 fee_rail TEXT NOT NULL, expires_at INTEGER NOT NULL, placed_at INTEGER NOT NULL,
 reserved INTEGER NOT NULL CHECK(reserved>=0),
 status TEXT NOT NULL CHECK(status IN ('open','filled','cancelled','expired')),
 UNIQUE(owner,nonce));
"""


class OrderMatchingMixin:
    @staticmethod
    def _order_reserve(order, quantity):
        return integer(quantity * (order["margin_per_lot"] + fee(order["price_cap"], order["fee_rail"])), "order reserve")

    def place_order(self, request_id, owner, nonce, side, quantity, limit_price,
                    margin_per_lot, expires_at, now, fee_rail="foreign"):
        identifier(owner, "owner")
        identifier(nonce, "nonce")
        if side not in ("long", "short"):
            raise ProtocolError("side must be long or short")
        integer(quantity, "quantity", 1)
        integer(limit_price, "limit_price", 1)
        integer(margin_per_lot, "margin_per_lot", 1, MAX_INT // 2)
        integer(expires_at, "expires_at", 1)
        integer(now, "now")
        fee(0, fee_rail)
        # Both directions have a collateral-derived maximum fill price. A
        # short's limit is its minimum; its budget cap is a separate ceiling.
        price_cap = integer(margin_per_lot * 10000 // self.config.initial_margin_bps, "collateral price cap", 1)
        if price_cap < limit_price:
            raise ProtocolError("per-lot margin does not cover the limit price")
        if side == "long":
            price_cap = limit_price
        integer(quantity * price_cap, "maximum order notional", 1)
        params = dict(owner=owner, nonce=nonce, side=side, quantity=quantity,
                      limit_price=limit_price, margin_per_lot=margin_per_lot,
                      expires_at=expires_at, fee_rail=fee_rail)
        order_id = digest(dict(domain="STATIC-SIM-PERP-ORDER-v1", **params))

        def action(db):
            prior = db.execute("SELECT * FROM perp_orders WHERE owner=? AND nonce=?", (owner, nonce)).fetchone()
            if prior:
                if prior["id"] != order_id:
                    raise ProtocolError("nonce already used for another perpetual intent")
                return dict(prior)
            if expires_at <= now:
                raise ProtocolError("perpetual order already expired")
            if db.execute("SELECT 1 FROM perp_orders WHERE owner=? AND side<>? AND status='open' AND expires_at>? LIMIT 1", (owner, side, now)).fetchone():
                raise ProtocolError("cancel opposite-side open intents first; self-trade guard")
            reserved = self._order_reserve(dict(margin_per_lot=margin_per_lot, price_cap=price_cap, fee_rail=fee_rail), quantity)
            self._cash(db, owner, -reserved)
            db.execute("""INSERT INTO perp_orders(id,owner,nonce,side,quantity,remaining,limit_price,
                margin_per_lot,price_cap,fee_rail,expires_at,placed_at,reserved,status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'open')""",
                (order_id, owner, nonce, side, quantity, quantity, limit_price,
                 margin_per_lot, price_cap, fee_rail, expires_at, now, reserved))
            return dict(db.execute("SELECT * FROM perp_orders WHERE id=?", (order_id,)).fetchone())

        return self._mutate(request_id, "place_order", params, now, action)

    def cancel_order(self, request_id, order_id, owner, now):
        identifier(order_id, "order_id")
        identifier(owner, "owner")

        def action(db):
            order = db.execute("SELECT * FROM perp_orders WHERE id=? AND owner=?", (order_id, owner)).fetchone()
            if order is None:
                raise ProtocolError("perpetual order not found for owner")
            if order["status"] == "cancelled":
                return dict(order)
            if order["status"] != "open":
                raise ProtocolError("perpetual order is no longer open")
            self._release_order(db, order, "cancelled")
            return dict(db.execute("SELECT * FROM perp_orders WHERE id=?", (order_id,)).fetchone())

        return self._mutate(request_id, "cancel_order", dict(order_id=order_id, owner=owner), now, action)

    def _release_order(self, db, order, status):
        self._cash(db, order["owner"], order["reserved"])
        db.execute("UPDATE perp_orders SET reserved=0,status=? WHERE id=?", (status, order["id"]))

    def _fill_order(self, db, order, quantity, price):
        charged = fee(integer(quantity * price, "fill notional", 1), order["fee_rail"])
        remaining = order["remaining"] - quantity
        target = self._order_reserve(order, remaining)
        released = order["reserved"] - target
        margin = integer(quantity * order["margin_per_lot"], "filled margin", 1)
        # Per-lot fee reservation covers all possible partial-fill partitions.
        refund = integer(released - margin - charged, "fee reservation refund")
        self._cash(db, order["owner"], refund)
        status = "open" if remaining else "filled"
        db.execute("UPDATE perp_orders SET remaining=?,reserved=?,status=? WHERE id=?", (remaining, target, status, order["id"]))
        order.update(remaining=remaining, reserved=target, status=status)
        return margin, charged

    def match_orders(self, request_id, now, max_inputs=80):
        integer(max_inputs, "max_inputs", 1, 80)

        def action(db):
            oracle = self._oracle(db, now)
            price = oracle["mark_price"]
            expired = db.execute("SELECT * FROM perp_orders WHERE status='open' AND expires_at<=? ORDER BY seq", (now,)).fetchall()
            for order in expired:
                self._release_order(db, order, "expired")
            cursor = self._meta(db, "orders_cursor")
            orders = [dict(o) for o in db.execute("SELECT * FROM perp_orders WHERE status='open' AND seq>? ORDER BY seq LIMIT ?", (cursor, max_inputs))]
            if len(orders) < max_inputs:
                orders += [dict(o) for o in db.execute("SELECT * FROM perp_orders WHERE status='open' AND seq<=? ORDER BY seq LIMIT ?", (cursor, max_inputs - len(orders)))]
            if orders:
                self._set_meta(db, "orders_cursor", orders[-1]["seq"])
            eligible = [o for o in orders if price <= o["price_cap"] and (
                price <= o["limit_price"] if o["side"] == "long" else price >= o["limit_price"])]
            longs = sorted((o for o in eligible if o["side"] == "long"), key=lambda o: (-o["limit_price"], o["seq"]))
            shorts = sorted((o for o in eligible if o["side"] == "short"), key=lambda o: (o["limit_price"], o["seq"]))
            li = si = 0
            matches = []
            total_fee = 0
            while li < len(longs) and si < len(shorts):
                long, short = longs[li], shorts[si]
                if long["owner"] == short["owner"]:
                    raise ProtocolError("self-match rejected")
                safe_quantity = MAX_INT // (long["margin_per_lot"] + short["margin_per_lot"])
                quantity = min(long["remaining"], short["remaining"], safe_quantity)
                integer(quantity, "match quantity", 1)
                long_margin, long_fee = self._fill_order(db, long, quantity, price)
                short_margin, short_fee = self._fill_order(db, short, quantity, price)
                pair_id = digest(dict(domain="STATIC-SIM-PERP-MATCH-v1", request_id=request_id,
                                      match=len(matches), long_order=long["id"], short_order=short["id"]))
                self._insert_pair(db, pair_id, long["owner"], short["owner"], quantity, price,
                                  long_margin, short_margin, long["fee_rail"], short["fee_rail"], now)
                matches.append(dict(pair_id=pair_id, long_order=long["id"], short_order=short["id"],
                                    quantity=quantity, entry_price=price, long_margin=long_margin,
                                    short_margin=short_margin, long_fee=long_fee, short_fee=short_fee))
                total_fee += long_fee + short_fee
                if not long["remaining"]:
                    li += 1
                if not short["remaining"]:
                    si += 1
            self._set_meta(db, "fees", self._meta(db, "fees") + total_fee)
            return dict(oracle_sequence=oracle["sequence"], execution="mock_mark_limit_match",
                        entry_price=price, input_ids=[o["id"] for o in orders],
                        expired_ids=[o["id"] for o in expired], matches=matches,
                        matched_quantity=sum(m["quantity"] for m in matches), entry_fees=total_fee)

        return self._mutate(request_id, "match_orders", dict(max_inputs=max_inputs), now, action)
