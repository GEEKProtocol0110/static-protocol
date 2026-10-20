# Perpetual orders and partial reductions

Version 0.3.0 extends the paired perpetual simulator. All amounts are fictional
integer quote atoms; the price is an operator-supplied mock mark. Read
[PERPETUALS.md](PERPETUALS.md) for equity, funding, liquidation and trust rules.

## Intent and collateral

Each intent specifies owner, nonce, long/short side, quantity, limit price,
margin per lot, expiry in simulation seconds, and fee rail. Its ID hashes a
domain-separated canonical immutable intent. Owner/nonce pairs remain consumed
after fill, cancellation or expiry. Repeating the same intent returns its
current record; changing intent under that nonce rejects. Request IDs separately
make each mutation retry-safe. Reusing an old nonce never creates a second order.

Placement moves free perpetual cash into an order reservation. It needs no
oracle observation. Define the collateral budget cap as:

```
budget_cap = floor(margin_per_lot * 10000 / initial_margin_bps)
long price_cap = limit_price
short price_cap = budget_cap
reserve(q) = q * (margin_per_lot + ceil(price_cap * fee_rate))
```

The budget cap must cover the limit price. A long's limit is its maximum entry
price; a short's limit is its minimum. A short additionally cannot fill above
its collateral budget cap. The recorded cap makes that bound inspectable.
Per-lot upward fee rounding covers any partition into partial fills. Actual
fees round upward on each matched notional; surplus returns to free cash.
Residual order reserves are recomputed exactly after every fill.

Amounts, notionals and reservations are bounded to signed 64-bit storage.
Quantity can split into multiple pairs where required to bound their combined
margin. One owner cannot place opposite-side unexpired open intents. This is
a label-level self-trade guard, not proof of distinct beneficial owners.

Cancellation checks the owner label and releases remaining reserves without
requiring a fresh price. A matching call expires overdue intents and refunds
them before admission. Expiry processing can touch more than 80 records; this
local bookkeeping is not a bounded on-chain transaction. Expired reservations
remain locked until cancellation or a successful matching call processes them.

## Matching at a mock mark

Matching requires a fresh accepted oracle observation. Up to 80 open orders
are admitted using a persistent rotating sequence cursor. Within that set,
longs rank by highest limit then earliest sequence; shorts by lowest limit then
earliest sequence. Eligible orders must cover the mark with their limit and
collateral cap. Repeatedly fill the overlap of the first eligible long/short,
creating an independently isolated pair for each overlap.

Entry uses that one mock mark. A crossing book away from the mark can remain
unfilled. This algorithm does not discover a market-clearing price or establish
executable liquidity. Priority is defined inside the admitted set, not globally
across rotated pages. The operator controls invocation and observations; no
MEV immunity, fair inclusion or live execution is claimed.

Each side's matched margin moves from its reservation into the pair, actual
entry fees accrue, and excess fee coverage returns to cash. The unfilled
intent stays open. A matching receipt records admitted and expired order IDs,
oracle sequence, created pair IDs, quantities, fees and entry price.
All changes and the receipt commit atomically.

## Reducing a pair

Either labeled counterparty can reduce a positive quantity no larger than the
current pair quantity. Both paired counterparties shrink together. This is
not a unilateral close against a replacement counterparty.

For a strict partial reduction of q from current quantity Q, original margins
mL/mS, cumulative long funding F, entry e and fresh mark p:

```
closed_margin_L = floor(mL * q / Q)
closed_margin_S = floor(mS * q / Q)
closed_funding = trunc_toward_zero(F * q / Q)
realized_long_PnL = q * (p - e)
realized_short_PnL = -realized_long_PnL
closed_long_equity = closed_margin_L + realized_long_PnL - closed_funding
closed_short_equity = closed_margin_S + realized_short_PnL + closed_funding
```

Deduct the closed margins and funding from the pair, preserving all rounding
dust in the residual. Entry stays unchanged. Future funding uses the remaining
quantity. Deduct exit fees from the nonnegative closed payouts, capped at each
payout, and credit free cash. A receipt records realized PnL, allocation,
payouts and residual risk. Repeated reductions can incur more rounded fees.

Before a strict partial reduction, both original sides must meet maintenance.
Both closed equities must be nonnegative, and both residual sides must meet
maintenance at the same mark. Any failure rejects the whole transaction; even
a currently healthy pair can fail a dust-sized reduction. Partial reductions
cannot consume insurance or isolate a deficit into a discarded slice.

Reducing the full current quantity follows the existing full-close rules,
including finite insurance and disclosed payout haircuts where needed. There
is no residual maintenance check. Liquidation remains full-pair only.

## Conservation and receipts

```
net simulated deposits - simulated withdrawals
  = free cash + open-order reserves + open-pair escrow + insurance + fees
```

Each open order must have its exact remaining reserve; terminal orders reserve
zero. Every mutation checks conservation and commits to accounts, orders,
pairs, funding, oracle, risk configuration and metadata. Transactions, request
retries, order nonces and receipt history persist across restarts. Hashes remain
local consistency checks without signatures or external anchors.

## Upgrading a v0.2 database

Opening schema 1 with unchanged risk configuration verifies its legacy state
commitment, accounting invariants and entire receipt chain before upgrading
to schema 2. Migration adds the empty orders state and admission cursor, then
appends one schema-migration receipt linked to the old head. All previous
receipt payloads/hashes, collateral, positions and funding remain unchanged.
Reopening schema 2 adds no duplicate migration receipt. Corrupted legacy state
or history rejects the upgrade atomically.

Keep a database backup before upgrading. Downgrading to v0.2 is unsupported.
The committed `tests/fixtures/perps-v0.2.sql` was exported from the actual v0.2
implementation; migration tests verify its old receipt prefix and balances.

## Run the flow

```bash
python -m static_protocol orders-demo
python -m unittest discover -s tests -v
```

The demo matches five lots across two pairs, accrues funding, reduces one pair
by a lot, closes all remaining positions, and audits 1,500,000 fictional
collateral atoms. Its output is in
[orders-output.json](../examples/orders-output.json). HTTP routes and example
payloads are documented in [API.md](API.md).
