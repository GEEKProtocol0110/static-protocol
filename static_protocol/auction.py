"""Pure uniform-price call auction. No oracle and no MEV-free claim."""

from dataclasses import dataclass
from .types import ProtocolError


@dataclass(frozen=True)
class AuctionResult:
    price: int | None
    volume: int
    fills: dict[str, int]


def clear(orders):
    """Max volume, then minimum imbalance, then lowest candidate limit tick.

    Allocate by price priority then ascending order commitment. Commitments
    are deterministic tie-breakers, NOT Sybil/grinding protection.
    The caller must supply one market and a fixed batch membership.
    """
    if len({o["id"] for o in orders}) != len(orders):
        raise ProtocolError("duplicate order in batch")
    if len(orders) > 80:
        raise ProtocolError("batch exceeds 80 modeled intake buffers")
    best = None
    for price in sorted({o["limit_price"] for o in orders}):
        demand = sum(o["remaining"] for o in orders if o["side"] == "buy" and o["limit_price"] >= price)
        supply = sum(o["remaining"] for o in orders if o["side"] == "sell" and o["limit_price"] <= price)
        volume = min(demand, supply)
        score = (-volume, abs(demand - supply), price)
        if volume and (best is None or score < best[0]):
            best = (score, price, volume)
    if best is None:
        return AuctionResult(None, 0, {})
    _, price, volume = best
    fills = {}
    for side in ("buy", "sell"):
        eligible = [o for o in orders if o["side"] == side and (
            o["limit_price"] >= price if side == "buy" else o["limit_price"] <= price)]
        eligible.sort(key=lambda o: ((-1 if side == "buy" else 1) * o["limit_price"], o["id"]))
        left = volume
        for order in eligible:
            if left == 0:
                break
            amount = min(left, order["remaining"])
            fills[order["id"]] = amount
            left -= amount
    return AuctionResult(price, volume, fills)
