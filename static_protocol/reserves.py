"""Standalone reserve economics model; no token issuance or chain custody."""

from dataclasses import dataclass
from fractions import Fraction

from .types import ProtocolError, integer


def ceil_fraction(value):
    return -(-value.numerator // value.denominator)


@dataclass
class LinearReserve:
    """Price f(s)=intercept+slope*s, measured in quote atoms per token lot.

    The v1 whitepaper does not select a curve or bootstrap/graduation policy.
    This reference chooses a linear curve. Buy rounding goes into reserve;
    sell rounding stays in reserve. Crossing the threshold is a signal only.
    """

    intercept: int = 100
    slope: int = 1
    max_supply: int = 1_000_000
    fibonacci_divisor: int = 233
    graduation_threshold: int = 1_000_000
    supply: int = 0
    reserve: int = 0

    def __post_init__(self):
        integer(self.intercept, "intercept", 1)
        integer(self.slope, "slope")
        integer(self.max_supply, "max_supply", 1)
        if self.fibonacci_divisor not in (144, 233, 377):
            raise ProtocolError("supported Fibonacci divisor: 144, 233, 377")
        integer(self.graduation_threshold, "graduation_threshold", 1)
        self.audit()

    def integral(self, start, end):
        return Fraction(self.intercept * (end - start)) + Fraction(self.slope * (end * end - start * start), 2)

    @property
    def graduation_ready(self):
        return self.reserve >= self.graduation_threshold

    def quote_buy(self, lots):
        integer(lots, "lots", 1)
        if lots > self.max_supply // self.fibonacci_divisor:
            raise ProtocolError("per-transaction issuance cap exceeded")
        if self.supply + lots > self.max_supply:
            raise ProtocolError("maximum supply exceeded")
        return integer(ceil_fraction(self.integral(self.supply, self.supply + lots)), "cost", 1)

    def buy(self, lots, max_cost):
        integer(max_cost, "max_cost", 1)
        cost = self.quote_buy(lots)
        if cost > max_cost:
            raise ProtocolError("buy slippage bound exceeded")
        integer(self.reserve + cost, "reserve")
        self.supply += lots
        self.reserve += cost
        self.audit()
        return cost

    def quote_sell(self, lots):
        integer(lots, "lots", 1)
        if lots > self.supply:
            raise ProtocolError("burn exceeds circulating supply")
        value = self.integral(self.supply - lots, self.supply)
        return value.numerator // value.denominator

    def sell(self, lots, min_return=0):
        integer(min_return, "min_return")
        returned = self.quote_sell(lots)
        if returned < min_return:
            raise ProtocolError("sell slippage bound exceeded")
        if returned > self.reserve:
            raise ProtocolError("insufficient reserve")
        self.supply -= lots
        self.reserve -= returned
        self.audit()
        return returned

    def audit(self):
        integer(self.supply, "supply", 0, self.max_supply)
        integer(self.reserve, "reserve")
        if self.reserve < ceil_fraction(self.integral(0, self.supply)):
            raise ProtocolError("curve is undercollateralized")
        return True


@dataclass
class ConstantProductPool:
    """Standalone integer AMM model. It is not connected to graduation.

    No pool fee in this reference; ceil-div output math preserves x*y.
    """

    base_reserve: int
    quote_reserve: int

    def __post_init__(self):
        integer(self.base_reserve, "base_reserve", 1)
        integer(self.quote_reserve, "quote_reserve", 1)

    def swap(self, asset_in, amount_in, min_output=1):
        integer(amount_in, "amount_in", 1)
        integer(min_output, "min_output", 1)
        if asset_in not in ("base", "quote"):
            raise ProtocolError("asset_in must be base or quote")
        x, y = ((self.base_reserve, self.quote_reserve) if asset_in == "base"
                else (self.quote_reserve, self.base_reserve))
        new_x = integer(x + amount_in, "new reserve", 1)
        new_y = (x * y + new_x - 1) // new_x
        output = y - new_y
        if output < min_output:
            raise ProtocolError("AMM slippage bound exceeded")
        if asset_in == "base":
            self.base_reserve, self.quote_reserve = new_x, new_y
        else:
            self.quote_reserve, self.base_reserve = new_x, new_y
        return output
