"""Persistent paired perpetuals reference model. Fictional quote collateral only.

The operator supplies prices, time, rate, identities, and matched counterparties.
Neither a price observation nor a receipt is a signature or consensus proof.
Full-pair settlement at a mock mark is an accounting model, not an executable
order-book liquidation or a promise of liquidity at that price.
"""

from dataclasses import asdict, dataclass
import json

from .engine import Engine
from .types import MAX_INT, QUOTE, ProtocolError, canonical, digest, fee, identifier, integer
from .perp_orders import ORDER_SCHEMA, OrderMatchingMixin

PERP_MARKET = "STATIC_TEST-PERP/USD_TEST"

SCHEMA = """
CREATE TABLE IF NOT EXISTS perp_meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS perp_config(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS perp_accounts(owner TEXT PRIMARY KEY, cash INTEGER NOT NULL CHECK(cash>=0));
CREATE TABLE IF NOT EXISTS perp_funding_log(
 seq INTEGER PRIMARY KEY, owner TEXT, amount INTEGER NOT NULL CHECK(amount>0),
 direction TEXT NOT NULL CHECK(direction IN ('in','out')), kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS perp_oracle(
 id INTEGER PRIMARY KEY CHECK(id=1), sequence INTEGER NOT NULL,
 index_price INTEGER NOT NULL CHECK(index_price>0), mark_price INTEGER NOT NULL CHECK(mark_price>0),
 observed_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS perp_pairs(
 seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
 long_owner TEXT NOT NULL, short_owner TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0),
 entry_price INTEGER NOT NULL CHECK(entry_price>0),
 long_margin INTEGER NOT NULL CHECK(long_margin>=0), short_margin INTEGER NOT NULL CHECK(short_margin>=0),
 funding_long INTEGER NOT NULL, long_rail TEXT NOT NULL, short_rail TEXT NOT NULL,
 opened_at INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('open','closed','liquidated')),
 CHECK(long_owner<>short_owner));
CREATE TABLE IF NOT EXISTS perp_receipts(
 seq INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
 request_hash TEXT NOT NULL, payload TEXT NOT NULL, hash TEXT NOT NULL);
"""


@dataclass(frozen=True)
class RiskConfig:
    initial_margin_bps: int = 2000
    maintenance_margin_bps: int = 1000
    oracle_max_age: int = 60
    mark_index_band_bps: int = 500
    funding_interval: int = 3600
    funding_cap_bps: int = 100

    def __post_init__(self):
        integer(self.initial_margin_bps, "initial_margin_bps", 1, 10000)
        integer(self.maintenance_margin_bps, "maintenance_margin_bps", 1, self.initial_margin_bps - 1)
        integer(self.oracle_max_age, "oracle_max_age", 1)
        integer(self.mark_index_band_bps, "mark_index_band_bps", 0, 10000)
        integer(self.funding_interval, "funding_interval", 1)
        integer(self.funding_cap_bps, "funding_cap_bps", 0, 10000)


def margin_required(notional, bps):
    integer(notional, "notional")
    return (notional * bps + 9999) // 10000


class PerpetualEngine(OrderMatchingMixin):
    def __init__(self, path, config=None):
        self.store = Engine(path)
        self.config = RiskConfig() if config is None else config
        if not isinstance(self.config, RiskConfig):
            raise ProtocolError("config must be a RiskConfig")
        self.config_hash = digest(asdict(self.config))
        with self.store._db(write=True) as db:
            for statement in (SCHEMA + ORDER_SCHEMA).split(";"):
                if statement.strip():
                    db.execute(statement)
            defaults = {"schema": 2, "time": 0, "fees": 0, "insurance": 0,
                        "uncovered_loss": 0, "funding_epoch": 0, "funding_at": 0}
            for key, value in defaults.items():
                db.execute("INSERT OR IGNORE INTO perp_meta VALUES (?,?)", (key, value))
            if self._meta(db, "schema") not in (1, 2):
                raise ProtocolError("unsupported perpetual schema")
            settings = canonical(asdict(self.config))
            db.execute("INSERT OR IGNORE INTO perp_config VALUES (1,?)", (settings,))
            if db.execute("SELECT value FROM perp_config WHERE id=1").fetchone()[0] != settings:
                raise ProtocolError("stored perpetual risk configuration differs; explicit migration required")
            if self._meta(db, "schema") == 1:
                self._verify_state(db)
                self._assert_invariants(db)
                _, prior_head = self._receipt_chain(db)
                self._set_meta(db, "schema", 2)
                db.execute("INSERT INTO perp_meta VALUES ('orders_cursor',0)")
                self._assert_invariants(db)
                now = self._meta(db, "time")
                self._record(db, "system:schema:2:" + prior_head[:32], "schema_migration",
                             digest(dict(from_schema=1, to_schema=2, prior_head=prior_head)), now,
                             dict(from_schema=1, to_schema=2, prior_head=prior_head))
            else:
                db.execute("INSERT OR IGNORE INTO perp_meta VALUES ('orders_cursor',0)")

    @staticmethod
    def _meta(db, key):
        return db.execute("SELECT value FROM perp_meta WHERE key=?", (key,)).fetchone()[0]

    @staticmethod
    def _set_meta(db, key, value):
        integer(value, key)
        db.execute("UPDATE perp_meta SET value=? WHERE key=?", (value, key))

    @staticmethod
    def _cash(db, owner, delta):
        db.execute("INSERT OR IGNORE INTO perp_accounts VALUES (?,0)", (owner,))
        current = db.execute("SELECT cash FROM perp_accounts WHERE owner=?", (owner,)).fetchone()[0]
        target = integer(current + delta, "available perpetual collateral")
        db.execute("UPDATE perp_accounts SET cash=? WHERE owner=?", (target, owner))

    def _mutate(self, request_id, kind, params, now, action):
        identifier(request_id, "request_id")
        integer(now, "now")
        request_hash = digest(dict(domain="STATIC-SIM-PERP-REQUEST-v1", kind=kind, params=params, now=now))
        with self.store._db(write=True) as db:
            self._verify_state(db)
            prior = db.execute("SELECT * FROM perp_receipts WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if prior["request_hash"] != request_hash:
                    raise ProtocolError("request_id already used with different perpetual parameters")
                return {**json.loads(prior["payload"]), "hash": prior["hash"]}
            if now < self._meta(db, "time"):
                raise ProtocolError("perpetual simulation time cannot move backward")
            self._set_meta(db, "time", now)
            result = action(db)
            self._assert_invariants(db)
            return self._record(db, request_id, kind, request_hash, now, result)

    def _record(self, db, request_id, kind, request_hash, now, result):
        prior = db.execute("SELECT seq,hash FROM perp_receipts ORDER BY seq DESC LIMIT 1").fetchone()
        body = dict(version=1, mode="simulation", market=PERP_MARKET,
                    sequence=1 if prior is None else prior["seq"] + 1,
                    request_id=request_id, request_hash=request_hash, kind=kind, now=now,
                    schema=self._meta(db, "schema"), config_hash=self.config_hash,
                    previous_hash="0" * 64 if prior is None else prior["hash"],
                    state_hash=self._state_commitment(db), result=result)
        receipt_hash = digest(body)
        db.execute("INSERT INTO perp_receipts(request_id,request_hash,payload,hash) VALUES (?,?,?,?)",
                   (request_id, request_hash, canonical(body), receipt_hash))
        return {**body, "hash": receipt_hash}

    def fund_account(self, request_id, owner, amount, now, direction="in"):
        identifier(owner, "owner")
        integer(amount, "amount", 1)
        if direction not in ("in", "out"):
            raise ProtocolError("direction must be in or out")

        def action(db):
            self._cash(db, owner, amount if direction == "in" else -amount)
            db.execute("INSERT INTO perp_funding_log(owner,amount,direction,kind) VALUES (?,?,?,'account')", (owner, amount, direction))
            return dict(owner=owner, amount=amount, direction=direction, asset=QUOTE)

        return self._mutate(request_id, "account_funding", dict(owner=owner, amount=amount, direction=direction), now, action)

    def fund_insurance(self, request_id, amount, now):
        integer(amount, "amount", 1)

        def action(db):
            self._set_meta(db, "insurance", self._meta(db, "insurance") + amount)
            db.execute("INSERT INTO perp_funding_log(owner,amount,direction,kind) VALUES (NULL,?,'in','insurance')", (amount,))
            return dict(amount=amount, asset=QUOTE)

        return self._mutate(request_id, "insurance_funding", dict(amount=amount), now, action)

    def publish_oracle(self, request_id, index_price, mark_price, observed_at, now):
        integer(index_price, "index_price", 1)
        integer(mark_price, "mark_price", 1)
        integer(observed_at, "observed_at")
        integer(now, "now")
        if not 0 <= now - observed_at <= self.config.oracle_max_age:
            raise ProtocolError("mock oracle observation is stale or in the future")
        if abs(mark_price - index_price) * 10000 > index_price * self.config.mark_index_band_bps:
            raise ProtocolError("mark/index deviation exceeds configured band")

        def action(db):
            prior = db.execute("SELECT * FROM perp_oracle WHERE id=1").fetchone()
            if prior and observed_at < prior["observed_at"]:
                raise ProtocolError("oracle observation time cannot move backward")
            for pair in db.execute("SELECT * FROM perp_pairs WHERE status='open'"):
                self._risk(pair, mark_price)
            sequence = 1 if prior is None else prior["sequence"] + 1
            db.execute("INSERT OR REPLACE INTO perp_oracle VALUES (1,?,?,?,?)", (sequence, index_price, mark_price, observed_at))
            return dict(sequence=sequence, index_price=index_price, mark_price=mark_price, observed_at=observed_at, authenticated=False)

        return self._mutate(request_id, "mock_oracle", dict(index_price=index_price, mark_price=mark_price, observed_at=observed_at), now, action)

    def _oracle(self, db, now):
        oracle = db.execute("SELECT * FROM perp_oracle WHERE id=1").fetchone()
        if oracle is None or not 0 <= now - oracle["observed_at"] <= self.config.oracle_max_age:
            raise ProtocolError("fresh mock oracle required")
        return oracle

    @staticmethod
    def _pair(db, pair_id):
        identifier(pair_id, "pair_id")
        pair = db.execute("SELECT * FROM perp_pairs WHERE id=?", (pair_id,)).fetchone()
        if pair is None or pair["status"] != "open":
            raise ProtocolError("open perpetual pair not found")
        return pair

    @staticmethod
    def _insert_pair(db, pair_id, long_owner, short_owner, quantity, entry_price,
                     long_margin, short_margin, long_rail, short_rail, now):
        integer(long_margin + short_margin, "pair collateral", 1)
        db.execute("""INSERT INTO perp_pairs(id,long_owner,short_owner,quantity,entry_price,
            long_margin,short_margin,funding_long,long_rail,short_rail,opened_at,status)
            VALUES (?,?,?,?,?,?,?,0,?,?,?,'open')""",
            (pair_id, long_owner, short_owner, quantity, entry_price, long_margin,
             short_margin, long_rail, short_rail, now))

    def open_pair(self, request_id, long_owner, short_owner, quantity, long_margin, short_margin,
                  now, long_rail="foreign", short_rail="foreign"):
        identifier(long_owner, "long_owner")
        identifier(short_owner, "short_owner")
        if long_owner == short_owner:
            raise ProtocolError("long and short owners must differ")
        integer(quantity, "quantity", 1)
        integer(long_margin, "long_margin", 1)
        integer(short_margin, "short_margin", 1)
        integer(long_margin + short_margin, "pair collateral")
        fee(0, long_rail)
        fee(0, short_rail)
        params = dict(long_owner=long_owner, short_owner=short_owner, quantity=quantity,
                      long_margin=long_margin, short_margin=short_margin, long_rail=long_rail, short_rail=short_rail)

        def action(db):
            oracle = self._oracle(db, now)
            notional = integer(quantity * oracle["mark_price"], "entry notional", 1)
            minimum = margin_required(notional, self.config.initial_margin_bps)
            if min(long_margin, short_margin) < minimum:
                raise ProtocolError("initial margin requirement not met on both sides")
            fees = {"long": fee(notional, long_rail), "short": fee(notional, short_rail)}
            self._cash(db, long_owner, -long_margin - fees["long"])
            self._cash(db, short_owner, -short_margin - fees["short"])
            self._set_meta(db, "fees", self._meta(db, "fees") + sum(fees.values()))
            pair_id = digest(dict(domain="STATIC-SIM-PERP-PAIR-v1", request_id=request_id, **params))
            self._insert_pair(db, pair_id, long_owner, short_owner, quantity,
                              oracle["mark_price"], long_margin, short_margin, long_rail, short_rail, now)
            return dict(pair_id=pair_id, entry_price=oracle["mark_price"], quantity=quantity,
                        initial_margin_required=minimum, entry_fees=fees, oracle_sequence=oracle["sequence"])

        return self._mutate(request_id, "open_pair", params, now, action)

    def _risk(self, pair, mark_price):
        notional = integer(pair["quantity"] * mark_price, "mark notional", 1)
        pnl = integer(pair["quantity"] * (mark_price - pair["entry_price"]), "unrealized PnL", -MAX_INT)
        long_equity = integer(pair["long_margin"] + pnl - pair["funding_long"], "long equity", -MAX_INT)
        short_equity = integer(pair["short_margin"] - pnl + pair["funding_long"], "short equity", -MAX_INT)
        maintenance = margin_required(notional, self.config.maintenance_margin_bps)
        return dict(mark_price=mark_price, notional=notional,
                    long_unrealized_pnl=pnl, short_unrealized_pnl=-pnl,
                    funding_long_paid=pair["funding_long"], long_equity=long_equity,
                    short_equity=short_equity, maintenance_each=maintenance,
                    long_liquidatable=long_equity < maintenance,
                    short_liquidatable=short_equity < maintenance,
                    deficit=max(0, -long_equity) + max(0, -short_equity))

    def risk(self, pair_id, now):
        integer(now, "now")
        with self.store._db() as db:
            if now < self._meta(db, "time"):
                raise ProtocolError("risk query cannot use past simulation time")
            oracle = self._oracle(db, now)
            return dict(pair_id=pair_id, now=now, oracle_sequence=oracle["sequence"],
                        **self._risk(self._pair(db, pair_id), oracle["mark_price"]))

    def top_up(self, request_id, pair_id, owner, amount, now):
        identifier(owner, "owner")
        integer(amount, "amount", 1)

        def action(db):
            pair = self._pair(db, pair_id)
            if owner not in (pair["long_owner"], pair["short_owner"]):
                raise ProtocolError("owner is not a counterparty")
            column = "long_margin" if owner == pair["long_owner"] else "short_margin"
            target = integer(pair[column] + amount, "position margin", 1)
            integer(pair["long_margin"] + pair["short_margin"] + amount, "pair collateral")
            self._cash(db, owner, -amount)
            db.execute(f"UPDATE perp_pairs SET {column}=? WHERE id=?", (target, pair_id))
            return dict(pair_id=pair_id, owner=owner, added_margin=amount, margin=target)

        return self._mutate(request_id, "top_up", dict(pair_id=pair_id, owner=owner, amount=amount), now, action)

    def settle_funding(self, request_id, epoch, rate_bps, now):
        integer(epoch, "epoch", 1)
        integer(rate_bps, "rate_bps", -self.config.funding_cap_bps, self.config.funding_cap_bps)

        def action(db):
            if epoch != self._meta(db, "funding_epoch") + 1:
                raise ProtocolError("funding epoch must advance exactly once")
            if now - self._meta(db, "funding_at") < self.config.funding_interval:
                raise ProtocolError("funding interval has not elapsed")
            oracle = self._oracle(db, now)
            payments = []
            for row in db.execute("SELECT * FROM perp_pairs WHERE status='open' ORDER BY seq").fetchall():
                pair = dict(row)
                risk = self._risk(pair, oracle["mark_price"])
                if risk["long_liquidatable"] or risk["short_liquidatable"]:
                    raise ProtocolError("resolve liquidatable pairs before funding settlement")
                notional = integer(pair["quantity"] * oracle["index_price"], "funding index notional", 1)
                magnitude = notional * abs(rate_bps) // 10000
                payment = magnitude if rate_bps >= 0 else -magnitude
                pair["funding_long"] = integer(pair["funding_long"] + payment, "funding accumulator", -MAX_INT)
                self._risk(pair, oracle["mark_price"])  # Reject an overflowing post-funding state.
                db.execute("UPDATE perp_pairs SET funding_long=? WHERE id=?", (pair["funding_long"], pair["id"]))
                payments.append(dict(pair_id=pair["id"], long_paid=payment, short_paid=-payment))
            self._set_meta(db, "funding_epoch", epoch)
            self._set_meta(db, "funding_at", now)
            return dict(epoch=epoch, rate_bps=rate_bps, oracle_sequence=oracle["sequence"],
                        index_price=oracle["index_price"], payments=payments, protocol_funding_fee=0)

        return self._mutate(request_id, "funding", dict(epoch=epoch, rate_bps=rate_bps), now, action)

    def close_pair(self, request_id, pair_id, owner, now):
        identifier(owner, "owner")

        def action(db):
            pair = self._pair(db, pair_id)
            if owner not in (pair["long_owner"], pair["short_owner"]):
                raise ProtocolError("owner is not a counterparty")
            return self._settle(db, pair, now, liquidation=False)

        return self._mutate(request_id, "close_pair", dict(pair_id=pair_id, owner=owner), now, action)

    def liquidate(self, request_id, pair_id, now):
        return self._mutate(request_id, "liquidate", dict(pair_id=pair_id), now,
                            lambda db: self._settle(db, self._pair(db, pair_id), now, liquidation=True))

    def reduce_pair(self, request_id, pair_id, owner, quantity, now):
        identifier(owner, "owner")
        integer(quantity, "quantity", 1)

        def action(db):
            pair = dict(self._pair(db, pair_id))
            if owner not in (pair["long_owner"], pair["short_owner"]):
                raise ProtocolError("owner is not a counterparty")
            if quantity > pair["quantity"]:
                raise ProtocolError("reduction exceeds open quantity")
            if quantity == pair["quantity"]:
                return self._settle(db, pair, now, liquidation=False)
            oracle = self._oracle(db, now)
            before = self._risk(pair, oracle["mark_price"])
            if before["long_liquidatable"] or before["short_liquidatable"]:
                raise ProtocolError("resolve liquidatable pairs through full settlement")
            part = dict(pair, quantity=quantity)
            for column in ("long_margin", "short_margin"):
                part[column] = pair[column] * quantity // pair["quantity"]
            funding = abs(pair["funding_long"]) * quantity // pair["quantity"]
            part["funding_long"] = funding if pair["funding_long"] >= 0 else -funding
            remaining = dict(pair, quantity=pair["quantity"] - quantity)
            for column in ("long_margin", "short_margin", "funding_long"):
                remaining[column] = pair[column] - part[column]
            slice_risk = self._risk(part, oracle["mark_price"])
            if min(slice_risk["long_equity"], slice_risk["short_equity"]) < 0:
                raise ProtocolError("partial reduction cannot fund negative slice equity")
            after = self._risk(remaining, oracle["mark_price"])
            if after["long_liquidatable"] or after["short_liquidatable"]:
                raise ProtocolError("rounded residual would fall below maintenance")
            result = self._distribute(db, part, slice_risk)
            db.execute("UPDATE perp_pairs SET quantity=?,long_margin=?,short_margin=?,funding_long=? WHERE id=?",
                       (remaining["quantity"], remaining["long_margin"], remaining["short_margin"], remaining["funding_long"], pair_id))
            return dict(result, status="open", oracle_sequence=oracle["sequence"],
                        closed_quantity=quantity, remaining_quantity=remaining["quantity"],
                        long_realized_pnl=slice_risk["long_unrealized_pnl"],
                        short_realized_pnl=slice_risk["short_unrealized_pnl"],
                        execution="mock_mark_partial_pair", remaining_risk=after)

        return self._mutate(request_id, "reduce_pair", dict(pair_id=pair_id, owner=owner, quantity=quantity), now, action)

    def _settle(self, db, pair, now, liquidation):
        oracle = self._oracle(db, now)
        risk = self._risk(pair, oracle["mark_price"])
        if liquidation and not (risk["long_liquidatable"] or risk["short_liquidatable"]):
            raise ProtocolError("neither side is below maintenance margin")
        result = self._distribute(db, pair, risk)
        status = "liquidated" if liquidation else "closed"
        db.execute("UPDATE perp_pairs SET status=? WHERE id=?", (status, pair["id"]))
        return dict(result, status=status, oracle_sequence=oracle["sequence"],
                    closed_quantity=pair["quantity"], remaining_quantity=0,
                    long_realized_pnl=risk["long_unrealized_pnl"],
                    short_realized_pnl=risk["short_unrealized_pnl"],
                    execution="mock_mark_full_pair")

    def _distribute(self, db, pair, risk):
        insurance_used = min(self._meta(db, "insurance"), risk["deficit"])
        uncovered = risk["deficit"] - insurance_used
        pool = integer(pair["long_margin"] + pair["short_margin"] + insurance_used, "settlement pool")
        # At most one side can have negative equity because the equity sum is
        # the actual escrow. Uncovered debt is a disclosed winner haircut.
        long_gross = min(max(0, risk["long_equity"]), pool)
        short_gross = pool - long_gross
        exit_fees = dict(long=min(fee(risk["notional"], pair["long_rail"]), long_gross),
                         short=min(fee(risk["notional"], pair["short_rail"]), short_gross))
        long_paid, short_paid = long_gross - exit_fees["long"], short_gross - exit_fees["short"]
        self._cash(db, pair["long_owner"], long_paid)
        self._cash(db, pair["short_owner"], short_paid)
        self._set_meta(db, "insurance", self._meta(db, "insurance") - insurance_used)
        self._set_meta(db, "fees", self._meta(db, "fees") + sum(exit_fees.values()))
        self._set_meta(db, "uncovered_loss", self._meta(db, "uncovered_loss") + uncovered)
        return dict(pair_id=pair["id"],
                    **risk, insurance_used=insurance_used, uncovered_loss=uncovered,
                    long_haircut=max(0, risk["long_equity"]) - long_gross,
                    short_haircut=max(0, risk["short_equity"]) - short_gross,
                    long_payout=long_paid, short_payout=short_paid, exit_fees=exit_fees,
                    liquidation_penalty=0)

    def _assert_invariants(self, db):
        if db.execute("SELECT value FROM perp_config WHERE id=1").fetchone()[0] != canonical(asdict(self.config)):
            raise ProtocolError("perpetual risk configuration integrity failure")
        funded = sum(r[0] if r[1] == "in" else -r[0] for r in db.execute("SELECT amount,direction FROM perp_funding_log"))
        held = sum(r[0] for r in db.execute("SELECT cash FROM perp_accounts"))
        for pair in db.execute("SELECT * FROM perp_pairs"):
            if pair["long_owner"] == pair["short_owner"]:
                raise ProtocolError("perpetual counterparty invariant failed")
            for name in ("quantity", "entry_price", "long_margin", "short_margin"):
                integer(pair[name], name, 1 if name in ("quantity", "entry_price") else 0)
            integer(pair["funding_long"], "funding_long", -MAX_INT)
            if pair["status"] == "open":
                held += pair["long_margin"] + pair["short_margin"]
        held += self._meta(db, "fees") + self._meta(db, "insurance")
        for order in db.execute("SELECT * FROM perp_orders"):
            integer(order["remaining"], "remaining order quantity", 0, order["quantity"])
            target = self._order_reserve(order, order["remaining"]) if order["status"] == "open" else 0
            if order["reserved"] != target:
                raise ProtocolError("perpetual order reserve invariant failed")
            held += target
        if held != funded:
            raise ProtocolError("perpetual collateral conservation failure")
        return dict(net_simulated_funding=funded, held_collateral=held)

    @staticmethod
    def _payload(row):
        try:
            body = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProtocolError("perpetual receipt encoding integrity failure") from exc
        if not isinstance(body, dict):
            raise ProtocolError("perpetual receipt must encode an object")
        return body

    @staticmethod
    def _state_commitment(db):
        data = {}
        tables = [("perp_meta", "key"), ("perp_config", "id"),
                  ("perp_accounts", "owner"), ("perp_funding_log", "seq"),
                  ("perp_oracle", "id"), ("perp_pairs", "seq")]
        if PerpetualEngine._meta(db, "schema") >= 2:
            tables.append(("perp_orders", "seq"))
        for table, order in tables:
            data[table] = [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY {order}")]
        return digest(data)

    def _verify_state(self, db):
        latest = db.execute("SELECT payload FROM perp_receipts ORDER BY seq DESC LIMIT 1").fetchone()
        if latest and self._payload(latest).get("state_hash") != self._state_commitment(db):
            raise ProtocolError("perpetual state commitment integrity failure")

    def audit(self):
        with self.store._db() as db:
            totals = self._assert_invariants(db)
            self._verify_state(db)
            count, previous = self._receipt_chain(db)
            return dict(mode="simulation", market=PERP_MARKET, conservation="passed",
                        receipt_chain="passed", state_commitment="passed", receipts=count, head=previous,
                        uncovered_loss=self._meta(db, "uncovered_loss"), **totals,
                        chain_anchor=False, cryptographic_ownership=False)

    def _receipt_chain(self, db):
        previous = "0" * 64
        count = 0
        for row in db.execute("SELECT * FROM perp_receipts ORDER BY seq"):
            count += 1
            body = self._payload(row)
            if (digest(body) != row["hash"] or body.get("previous_hash") != previous
                    or body.get("sequence") != count or row["seq"] != count
                    or body.get("request_id") != row["request_id"]
                    or body.get("request_hash") != row["request_hash"]
                    or body.get("config_hash") != self.config_hash):
                raise ProtocolError("perpetual receipt chain integrity failure")
            previous = row["hash"]
        return count, previous

    def state(self):
        with self.store._db() as db:
            oracle = db.execute("SELECT * FROM perp_oracle WHERE id=1").fetchone()
            return dict(mode="simulation", market=PERP_MARKET, collateral_asset=QUOTE,
                        config=asdict(self.config), metadata={r[0]: r[1] for r in db.execute("SELECT key,value FROM perp_meta ORDER BY key")},
                        oracle=None if oracle is None else dict(oracle),
                        accounts=[dict(r) for r in db.execute("SELECT * FROM perp_accounts ORDER BY owner")],
                        pairs=[dict(r) for r in db.execute("SELECT * FROM perp_pairs ORDER BY seq")],
                        orders=[dict(r) for r in db.execute("SELECT * FROM perp_orders ORDER BY seq")],
                        receipts=[{**json.loads(r["payload"]), "hash": r["hash"]} for r in db.execute("SELECT * FROM perp_receipts ORDER BY seq")])
