"""Explicit fail-closed boundary for every live external dependency."""

from .types import ProtocolError


class LiveSettlementAdapter:
    def broadcast(self, transaction):
        raise ProtocolError("live broadcasting is disabled; no verified Kaspa transaction adapter")

    def convert_foreign_collateral(self, *args, **kwargs):
        raise ProtocolError("disabled: bridge finality, issuer risk, routing, and stablecoin lineage unverified")

    def convert_protocol_fees_to_kas(self, *args, **kwargs):
        raise ProtocolError("disabled: no authenticated price, executable quote, or conversion route")

    def graduate_curve(self, *args, **kwargs):
        raise ProtocolError("disabled: graduation transaction and liquidity seeding policy unspecified")
