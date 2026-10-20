"""SQLite-atomic local ledger modeling intake buffers and settlement.

All identities, assets, DAA values, and fee eligibility are simulation inputs.
Order hashes and receipt hashes are not signatures or consensus proofs.
"""

from contextlib import contextmanager
import json
import sqlite3

from .auction import clear
from .types import (BASE, QUOTE, MARKET, ProtocolError, integer, identifier,
                    buy_reserve, canonical, digest, fee)


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO meta VALUES ('daa', 0);
INSERT OR IGNORE INTO meta VALUES ('schema', 1);
INSERT OR IGNORE INTO meta VALUES ('sweep_cursor', 0);
CREATE TABLE IF NOT EXISTS balances(
 owner TEXT NOT NULL, asset TEXT NOT NULL, available INTEGER NOT NULL CHECK(available>=0),
 locked INTEGER NOT NULL CHECK(locked>=0), PRIMARY KEY(owner,asset));
CREATE TABLE IF NOT EXISTS funding(
 seq INTEGER PRIMARY KEY, owner TEXT NOT NULL, asset TEXT NOT NULL,
 amount INTEGER NOT NULL, direction TEXT NOT NULL CHECK(direction IN ('in','out')));
CREATE TABLE IF NOT EXISTS orders(
 seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL,
 nonce TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL,
 remaining INTEGER NOT NULL CHECK(remaining>=0), limit_price INTEGER NOT NULL,
 expiry_daa INTEGER NOT NULL, fee_rail TEXT NOT NULL, reserved INTEGER NOT NULL CHECK(reserved>=0),
 status TEXT NOT NULL, UNIQUE(owner,nonce));
CREATE TABLE IF NOT EXISTS protocol_fees(asset TEXT PRIMARY KEY, amount INTEGER NOT NULL CHECK(amount>=0));
INSERT OR IGNORE INTO protocol_fees VALUES ('USD_TEST',0);
CREATE TABLE IF NOT EXISTS receipts(
 seq INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
 request_hash TEXT NOT NULL, payload TEXT NOT NULL, hash TEXT NOT NULL);
"""


class Engine:
    def __init__(self, path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ProtocolError("use a file database; operations use separate connections")
        with self._db(write=True) as db:
            db.executescript(SCHEMA)
            if db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] != 1:
                raise ProtocolError("unsupported database schema")

    @contextmanager
    def _db(self, write=False):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _balance(db, owner, asset, available=0, locked=0):
        db.execute("INSERT OR IGNORE INTO balances VALUES (?,?,0,0)", (owner, asset))
        row = db.execute("SELECT available,locked FROM balances WHERE owner=? AND asset=?", (owner, asset)).fetchone()
        a, l = row[0] + available, row[1] + locked
        integer(a, "available balance")
        integer(l, "locked balance")
        integer(a + l, "total account balance")
        db.execute("UPDATE balances SET available=?,locked=? WHERE owner=? AND asset=?", (a, l, owner, asset))

    def fund(self, owner, asset, amount, direction="in"):
        identifier(owner, "owner")
        if asset not in (BASE, QUOTE):
            raise ProtocolError("only STATIC_TEST and USD_TEST exist in simulation")
        integer(amount, "amount", 1)
        if direction not in ("in", "out"):
            raise ProtocolError("direction must be in or out")
        with self._db(write=True) as db:
            self._balance(db, owner, asset, available=amount if direction == "in" else -amount)
            db.execute("INSERT INTO funding(owner,asset,amount,direction) VALUES (?,?,?,?)", (owner, asset, amount, direction))
            self._assert_invariants(db)
        return {"owner": owner, "asset": asset, "amount": amount, "direction": direction, "simulation": True}

    def submit(self, owner, nonce, side, quantity, limit_price, expiry_daa, fee_rail="foreign"):
        identifier(owner, "owner")
        identifier(nonce, "nonce")
        if side not in ("buy", "sell"):
            raise ProtocolError("side must be buy or sell")
        integer(quantity, "quantity", 1)
        integer(limit_price, "limit_price", 1)
        integer(quantity * limit_price, "order notional", 1)
        integer(expiry_daa, "expiry_daa", 1)
        fee(0, fee_rail)
        body = dict(owner=owner, nonce=nonce, side=side, quantity=quantity,
                    limit_price=limit_price, expiry_daa=expiry_daa, fee_rail=fee_rail)
        order_id = digest({"domain": "STATIC-SIM-ORDER-v1", "market": MARKET, **body})
        with self._db(write=True) as db:
            prior = db.execute("SELECT * FROM orders WHERE owner=? AND nonce=?", (owner, nonce)).fetchone()
            if prior:
                if prior["id"] == order_id:
                    return dict(prior)
                raise ProtocolError("nonce already used for a different intent")
            daa = db.execute("SELECT value FROM meta WHERE key='daa'").fetchone()[0]
            if expiry_daa <= daa:
                raise ProtocolError("order already expired at current simulation DAA")
            if db.execute("SELECT 1 FROM orders WHERE owner=? AND side<>? AND status='open' LIMIT 1", (owner, side)).fetchone():
                raise ProtocolError("cancel the owner's opposite-side orders first; simulation self-trade guard")
            reserved = buy_reserve(quantity, limit_price, fee_rail) if side == "buy" else quantity
            self._balance(db, owner, QUOTE if side == "buy" else BASE, available=-reserved, locked=reserved)
            db.execute("""INSERT INTO orders(id,owner,nonce,side,quantity,remaining,limit_price,
                       expiry_daa,fee_rail,reserved,status) VALUES (?,?,?,?,?,?,?,?,?,?,'open')""",
                       (order_id, owner, nonce, side, quantity, quantity, limit_price, expiry_daa, fee_rail, reserved))
            self._assert_invariants(db)
            return dict(db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())

    def cancel(self, owner, order_id):
        identifier(owner, "owner")
        with self._db(write=True) as db:
            order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if order is None or order["owner"] != owner:
                raise ProtocolError("order not found for owner")
            if order["status"] == "cancelled":
                return dict(order)
            if order["status"] != "open":
                raise ProtocolError("order is no longer open")
            self._release(db, order, "cancelled")
            self._assert_invariants(db)
            return dict(db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())

    def _release(self, db, order, status):
        asset = QUOTE if order["side"] == "buy" else BASE
        self._balance(db, order["owner"], asset, available=order["reserved"], locked=-order["reserved"])
        db.execute("UPDATE orders SET reserved=0,status=? WHERE id=?", (status, order["id"]))

    def sweep(self, request_id, daa, max_inputs=80):
        identifier(request_id, "request_id")
        integer(daa, "daa")
        integer(max_inputs, "max_inputs", 1, 80)
        request_hash = digest(dict(daa=daa, max_inputs=max_inputs, market=MARKET))
        with self._db(write=True) as db:
            prior = db.execute("SELECT * FROM receipts WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if prior["request_hash"] != request_hash:
                    raise ProtocolError("request_id reused with different sweep parameters")
                return {**json.loads(prior["payload"]), "hash": prior["hash"]}
            current = db.execute("SELECT value FROM meta WHERE key='daa'").fetchone()[0]
            if daa < current:
                raise ProtocolError("simulation DAA cannot move backward")
            db.execute("UPDATE meta SET value=? WHERE key='daa'", (daa,))
            expired = db.execute("SELECT * FROM orders WHERE status='open' AND expiry_daa<=? ORDER BY seq", (daa,)).fetchall()
            for order in expired:
                self._release(db, order, "expired")
            cursor = db.execute("SELECT value FROM meta WHERE key='sweep_cursor'").fetchone()[0]
            orders = [dict(o) for o in db.execute("SELECT * FROM orders WHERE status='open' AND seq>? ORDER BY seq LIMIT ?", (cursor, max_inputs))]
            if len(orders) < max_inputs:
                orders += [dict(o) for o in db.execute("SELECT * FROM orders WHERE status='open' AND seq<=? ORDER BY seq LIMIT ?", (cursor, max_inputs - len(orders)))]
            if orders:
                db.execute("UPDATE meta SET value=? WHERE key='sweep_cursor'", (orders[-1]["seq"],))
            result = clear(orders)
            fills = []
            total_fee = 0
            for order in orders:
                quantity = result.fills.get(order["id"], 0)
                if not quantity:
                    continue
                price = result.price
                notional = integer(quantity * price, "fill notional", 1)
                charged = fee(notional, order["fee_rail"])
                remaining = order["remaining"] - quantity
                if order["side"] == "buy":
                    target = buy_reserve(remaining, order["limit_price"], order["fee_rail"])
                    released = order["reserved"] - target
                    self._balance(db, order["owner"], QUOTE, available=released - notional - charged, locked=-released)
                    self._balance(db, order["owner"], BASE, available=quantity)
                else:
                    target = remaining
                    self._balance(db, order["owner"], BASE, locked=-quantity)
                    self._balance(db, order["owner"], QUOTE, available=notional - charged)
                db.execute("UPDATE orders SET remaining=?,reserved=?,status=? WHERE id=?", (remaining, target, "open" if remaining else "filled", order["id"]))
                fills.append(dict(order_id=order["id"], owner=order["owner"], side=order["side"], quantity=quantity, price=price, fee=charged))
                total_fee += charged
            fee_balance = db.execute("SELECT amount FROM protocol_fees WHERE asset=?", (QUOTE,)).fetchone()[0]
            integer(fee_balance + total_fee, "protocol fees")
            db.execute("UPDATE protocol_fees SET amount=amount+? WHERE asset=?", (total_fee, QUOTE))
            self._assert_invariants(db)
            previous = db.execute("SELECT seq,hash FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
            payload = dict(version=1, mode="simulation", market=MARKET,
                           sequence=1 if previous is None else previous["seq"] + 1,
                           request_id=request_id, request_hash=request_hash, daa=daa,
                           previous_hash="0" * 64 if previous is None else previous["hash"],
                           input_ids=[o["id"] for o in orders], expired_ids=[o["id"] for o in expired],
                           clearing_price=result.price, executed_volume=result.volume,
                           quote_fees=total_fee, fills=fills)
            receipt_hash = digest(payload)
            db.execute("INSERT INTO receipts(request_id,request_hash,payload,hash) VALUES (?,?,?,?)", (request_id, request_hash, canonical(payload), receipt_hash))
            return {**payload, "hash": receipt_hash}

    @staticmethod
    def _assert_invariants(db):
        # Python sums avoid SQLite's signed-64-bit aggregate overflow.
        for asset in (BASE, QUOTE):
            net = sum(r[0] if r[1] == "in" else -r[0] for r in db.execute("SELECT amount,direction FROM funding WHERE asset=?", (asset,)))
            held = sum(r[0] + r[1] for r in db.execute("SELECT available,locked FROM balances WHERE asset=?", (asset,)))
            held += sum(r[0] for r in db.execute("SELECT amount FROM protocol_fees WHERE asset=?", (asset,)))
            if held != net:
                raise ProtocolError(f"conservation failure for {asset}")
        expected = {}
        for o in db.execute("SELECT * FROM orders"):
            if o["status"] == "open":
                target = buy_reserve(o["remaining"], o["limit_price"], o["fee_rail"]) if o["side"] == "buy" else o["remaining"]
            else:
                target = 0
            if o["reserved"] != target:
                raise ProtocolError("order reserve invariant failed")
            key = (o["owner"], QUOTE if o["side"] == "buy" else BASE)
            expected[key] = expected.get(key, 0) + target
        for b in db.execute("SELECT * FROM balances"):
            if b["locked"] != expected.get((b["owner"], b["asset"]), 0):
                raise ProtocolError("lock accounting invariant failed")

    def audit(self):
        with self._db() as db:
            self._assert_invariants(db)
            previous = "0" * 64
            count = 0
            for row in db.execute("SELECT * FROM receipts ORDER BY seq"):
                body = json.loads(row["payload"])
                count += 1
                if (digest(body) != row["hash"] or body["previous_hash"] != previous
                        or body["sequence"] != count or row["seq"] != count
                        or body["request_id"] != row["request_id"]
                        or body["request_hash"] != row["request_hash"]):
                    raise ProtocolError("receipt chain integrity failure")
                previous = row["hash"]
            return dict(mode="simulation", conservation="passed", lock_accounting="passed",
                        receipt_chain="passed", receipts=count, head=previous,
                        chain_anchor=False, cryptographic_ownership=False)

    def state(self):
        with self._db() as db:
            return dict(mode="simulation", market=MARKET,
                        daa=db.execute("SELECT value FROM meta WHERE key='daa'").fetchone()[0],
                        balances=[dict(r) for r in db.execute("SELECT * FROM balances ORDER BY owner,asset")],
                        orders=[dict(r) for r in db.execute("SELECT * FROM orders ORDER BY seq")],
                        protocol_fees=[dict(r) for r in db.execute("SELECT * FROM protocol_fees")],
                        receipts=[{**json.loads(r["payload"]), "hash": r["hash"]} for r in db.execute("SELECT * FROM receipts ORDER BY seq")])
