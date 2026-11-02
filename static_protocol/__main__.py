import argparse
import json
import os
from pathlib import Path
import secrets
import tempfile

from .engine import Engine
from .reserves import LinearReserve, ConstantProductPool
from .treasury import Treasury, EPOCH_SECONDS
from .types import BASE, QUOTE
from . import __version__


def demo():
    with tempfile.TemporaryDirectory(prefix="static-demo-") as folder:
        engine = Engine(Path(folder) / "demo.sqlite")
        engine.fund("alice", QUOTE, 1_000_000)
        engine.fund("bob", BASE, 100)
        engine.submit("alice", "buy-1", "buy", 10, 110, 100, "native")
        engine.submit("bob", "sell-1", "sell", 10, 100, 100, "foreign")
        receipt = engine.sweep("batch-1", 1)
        curve = LinearReserve()
        paid = curve.buy(10, 2000)
        refunded = curve.sell(10)
        pool = ConstantProductPool(1000, 100000)
        treasury = Treasury()
        split = treasury.allocate_margin(1_000_000)
        treasury.begin_epoch(0)
        spent = treasury.drip_opex(EPOCH_SECONDS, 12_500)
        return dict(version=__version__, mode="simulation", receipt=receipt,
                    audit=engine.audit(), state=engine.state(),
                    independent_models={"curve": {"paid": paid, "refunded": refunded, "supply": curve.supply, "reserve": curve.reserve},
                                        "amm_quote_out": pool.swap("base", 10),
                                        "treasury_simulated_sompi": {"split": split, "opex_released": spent}},
                    live_broadcast=False)


def perps_demo():
    from .perps import PerpetualEngine
    with tempfile.TemporaryDirectory(prefix="static-perps-demo-") as folder:
        engine = PerpetualEngine(Path(folder) / "perps.sqlite")
        engine.fund_account("deposit-long", "alice", 500000, 0)
        engine.fund_account("deposit-short", "bob", 500000, 0)
        engine.fund_insurance("insurance", 50000, 0)
        engine.publish_oracle("oracle-1", 10000, 10000, 0, 0)
        opened = engine.open_pair("pair-1", "alice", "bob", 10, 20000, 20000, 0)
        pair_id = opened["result"]["pair_id"]
        initial_risk = engine.risk(pair_id, 0)
        engine.publish_oracle("oracle-2", 10000, 10000, 3600, 3600)
        funding = engine.settle_funding("funding-1", 1, 10, 3600)
        engine.top_up("top-up-1", pair_id, "bob", 5000, 3600)
        engine.publish_oracle("oracle-3", 12500, 12500, 3601, 3601)
        liquidation = engine.liquidate("liquidation-1", pair_id, 3601)
        return dict(version=__version__, mode="simulation", opened=opened,
                    initial_risk=initial_risk, funding=funding, liquidation=liquidation,
                    audit=engine.audit(), state=engine.state(), live_broadcast=False)


def orders_demo():
    from .perps import PerpetualEngine
    with tempfile.TemporaryDirectory(prefix="static-orders-demo-") as folder:
        engine = PerpetualEngine(Path(folder) / "orders.sqlite")
        for owner in ("alice", "bob", "carol"):
            engine.fund_account("deposit-"+owner, owner, 500000, 0)
        engine.publish_oracle("o1", 10000, 10000, 0, 0)
        engine.place_order("long-order", "alice", "long-1", "long", 5, 10100, 3000, 10000, 0)
        engine.place_order("short-order-1", "bob", "short-1", "short", 3, 9900, 3000, 10000, 0)
        engine.place_order("short-order-2", "carol", "short-1", "short", 2, 9950, 3000, 10000, 0)
        matched = engine.match_orders("match-1", 0)
        pair_id = matched["result"]["matches"][0]["pair_id"]
        engine.publish_oracle("o2", 10051, 10000, 3600, 3600)
        funding = engine.settle_funding("funding-1", 1, 100, 3600)
        engine.publish_oracle("o3", 10200, 10200, 3601, 3601)
        reduced = engine.reduce_pair("reduce-1", pair_id, "alice", 1, 3601)
        closed = engine.close_pair("close-1", pair_id, "alice", 3601)
        other = matched["result"]["matches"][1]["pair_id"]
        engine.close_pair("close-2", other, "carol", 3601)
        return dict(version=__version__, mode="simulation", matched=matched,
                    funding=funding, reduced=reduced, closed=closed,
                    audit=engine.audit(), state=engine.state(), live_broadcast=False)


def main():
    parser = argparse.ArgumentParser(description="STATIC local reference engine: simulated assets only")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="run an isolated complete example")
    commands.add_parser("perps-demo", help="run a simulated long/short, funding, and liquidation example")
    commands.add_parser("orders-demo", help="match perpetual limit orders and partially close a pair")
    serve = commands.add_parser("serve", help="start the loopback operator API")
    serve.add_argument("--db", default="static-sim.sqlite")
    serve.add_argument("--port", type=int, default=8787)
    audit = commands.add_parser("audit", help="check ledger and receipt integrity")
    audit.add_argument("--db", required=True)
    perp_audit = commands.add_parser("perps-audit", help="check perpetual collateral and receipt integrity")
    perp_audit.add_argument("--db", required=True)
    args = parser.parse_args()
    if args.command == "demo":
        print(json.dumps(demo(), indent=2))
    elif args.command == "perps-demo":
        print(json.dumps(perps_demo(), indent=2))
    elif args.command == "orders-demo":
        print(json.dumps(orders_demo(), indent=2))
    elif args.command == "audit":
        print(json.dumps(Engine(args.db).audit(), indent=2))
    elif args.command == "perps-audit":
        from .perps import PerpetualEngine
        print(json.dumps(PerpetualEngine(args.db).audit(), indent=2))
    else:
        from .api import make_server
        token = os.environ.get("STATIC_SIM_TOKEN") or secrets.token_urlsafe(32)
        server = make_server(Engine(args.db), token, args.port)
        print(f"STATIC simulator: http://127.0.0.1:{server.server_port}\nSimulation only. No live funds.\nOperator token: {token}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
