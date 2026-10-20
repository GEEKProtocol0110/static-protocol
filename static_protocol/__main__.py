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
        return dict(version="0.1.0", mode="simulation", receipt=receipt,
                    audit=engine.audit(), state=engine.state(),
                    independent_models={"curve": {"paid": paid, "refunded": refunded, "supply": curve.supply, "reserve": curve.reserve},
                                        "amm_quote_out": pool.swap("base", 10),
                                        "treasury_simulated_sompi": {"split": split, "opex_released": spent}},
                    live_broadcast=False)


def main():
    parser = argparse.ArgumentParser(description="STATIC local reference engine: simulated assets only")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="run an isolated complete example")
    serve = commands.add_parser("serve", help="start the loopback operator API")
    serve.add_argument("--db", default="static-sim.sqlite")
    serve.add_argument("--port", type=int, default=8787)
    audit = commands.add_parser("audit", help="check ledger and receipt integrity")
    audit.add_argument("--db", required=True)
    args = parser.parse_args()
    if args.command == "demo":
        print(json.dumps(demo(), indent=2))
    elif args.command == "audit":
        print(json.dumps(Engine(args.db).audit(), indent=2))
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
