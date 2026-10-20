"""50/25/25 accounting in simulated sompi, separate from exchange USD_TEST fees."""

from dataclasses import dataclass, field
from .types import ProtocolError, integer

EPOCH_SECONDS = 30 * 24 * 60 * 60


@dataclass
class Treasury:
    sovereign: int = 0
    backstop: int = 0
    opex: int = 0
    epoch_start: int | None = None
    epoch_basis: int = 0
    epoch_spent: int = 0
    last_time: int = 0
    gross_allocated: int = field(default=0, init=False)

    def allocate_margin(self, simulated_sompi):
        integer(simulated_sompi, "simulated_sompi", 1)
        # Cumulative split avoids systematic drift on repeated tiny margins.
        gross = self.gross_allocated
        total = integer(gross + simulated_sompi, "total margins")
        # Four-atom cycle: sovereign, backstop, sovereign, OpEx. Each
        # increment is nonnegative; every complete cycle is exactly 50/25/25.
        delta_sovereign = (total + 1) // 2 - (gross + 1) // 2
        delta_backstop = (total + 2) // 4 - (gross + 2) // 4
        delta_opex = simulated_sompi - delta_sovereign - delta_backstop
        self.sovereign += delta_sovereign
        self.backstop += delta_backstop
        self.opex += delta_opex
        self.gross_allocated = total
        return dict(sovereign=delta_sovereign, backstop=delta_backstop, opex=delta_opex)

    def begin_epoch(self, now):
        integer(now, "now")
        if now < self.last_time:
            raise ProtocolError("time cannot move backward")
        if self.epoch_start is not None and now < self.epoch_start + EPOCH_SECONDS:
            raise ProtocolError("current 30-day epoch is still active")
        self.epoch_start = now
        self.epoch_basis = self.opex
        self.epoch_spent = 0
        self.last_time = now

    def drip_opex(self, now, amount):
        integer(now, "now")
        integer(amount, "amount", 1)
        if self.epoch_start is None:
            raise ProtocolError("begin an epoch before spending")
        if now < self.last_time:
            raise ProtocolError("time cannot move backward")
        elapsed = min(now - self.epoch_start, EPOCH_SECONDS)
        entitlement = self.epoch_basis * elapsed // (20 * EPOCH_SECONDS)
        if self.epoch_spent + amount > entitlement or amount > self.opex:
            raise ProtocolError("linear 5%-per-30-day OpEx allowance exceeded")
        self.opex -= amount
        self.epoch_spent += amount
        self.last_time = now
        return amount

    def release_sovereign(self, *args, **kwargs):
        raise ProtocolError("disabled: no consensus-verified complete solvency proof or recapitalization auction")
