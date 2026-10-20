"""Wire-format validation and integer arithmetic shared by the engine."""

import hashlib
import json
import re


class ProtocolError(ValueError):
    """An expected invalid state transition or request."""


MAX_INT = 2**63 - 1
BASE = "STATIC_TEST"
QUOTE = "USD_TEST"
MARKET = f"{BASE}/{QUOTE}"
RAILS = {"foreign": (5, 10_000), "native": (25, 100_000)}


def integer(value, name, minimum=0, maximum=MAX_INT):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProtocolError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", value):
        raise ProtocolError(f"{name} must be 1-96 letters, digits, or _.:-")
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def fee(notional, rail):
    integer(notional, "notional")
    if not isinstance(rail, str) or rail not in RAILS:
        raise ProtocolError("fee_rail must be native or foreign")
    numerator, denominator = RAILS[rail]
    return (notional * numerator + denominator - 1) // denominator


def buy_reserve(quantity, price, rail):
    # Reserving per-lot rounded fees covers ANY sequence of partial fills.
    return integer(quantity * (price + fee(price, rail)), "buy reserve")
