# Paired perpetuals reference model

Version 0.3.0 implements one fictional linear contract,
`STATIC_TEST-PERP/USD_TEST`. Each lot represents one fictional base unit;
prices and collateral use integer quote atoms. No real USD value, token
redemption, non-custody, or chain execution is implied.

## What works

Eligible limit orders match one long with one short of equal quantity;
the operator can also create pairs through the direct research API.
Both post isolated collateral from their perpetual cash balances, and pay
entry fees separately. Each pair can be topped up, monitored, partially reduced, fully closed,
or fully liquidated. Both counterparties settle together. Spot balances,
reserve objects, and the treasury remain separate from perpetual collateral.

See [ORDERS-AND-REDUCTIONS.md](ORDERS-AND-REDUCTIONS.md) for order reservations,
matching, exact partial-close allocation and migration from v0.2 databases.

The module reuses SQLite transactions, with separate `perp_*` tables. Every
successful mutation produces a durable, retry-safe receipt and state
commitment. Existing spot databases are compatible without any value transfer.

## Chosen reference parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| Initial margin | 2,000 bps / 20% | Each side posts at least 20% of entry notional; at most 5x entry leverage |
| Maintenance | 1,000 bps / 10% | Liquidatable when equity is strictly below the rounded requirement |
| Mock price age | 60 simulation seconds | Open, risk, funding, close and liquidation reject older observations |
| Mark/index band | 500 bps / 5% | Reject a mark outside the band around its index |
| Funding interval | At least 3,600 simulation seconds | Minimum elapsed time between successive funding events |
| Funding rate cap | +/-100 bps per event | Operator-supplied rate bounded to +/-1% of current index notional |
| Fees | Foreign 0.050%; native 0.025% | Entry fees from free cash; exit fees capped at actual payout |

These are testable research policies selected for this implementation, not
validated production risk settings. Configuration is persisted on initialization.
Reopening with a different configuration requires an explicit future migration.

## Prices and time

`publish_oracle` accepts positive integer index/mark prices, an observation time,
and current simulation time. It rejects future or stale observations, rewinding
observation time, excessive mark/index divergence and prices that overflow open
positions' notional/equity bounds. Snapshots have increasing sequence numbers.
All mutations use monotonic operator-supplied `now`. Risk reads cannot use time
earlier than the last committed mutation. Receipt retries return their original
result, including after subsequent state changes.

This is an unauthenticated mock feed. Both index and mark are controlled by the
operator, so the band check cannot prevent manipulation. Entry and settlement
use the latest accepted mark inside the transaction. Limit orders enforce user
price bounds at that mark. Signed quotes, external aggregation, price discovery,
premium estimation and executable fills remain future requirements.

## Equity and margin

For quantity q, entry e, mark p, posted margins mL/mS, and cumulative funding
paid by the long F:

```
long PnL = q * (p - e)
short PnL = -long PnL
long equity = mL + long PnL - F
short equity = mS - long PnL + F
maintenance for each side = ceil(q * p * maintenance_bps / 10000)
```

Both sides must post `ceil(q * e * initial_margin_bps / 10000)` or more.
Entry fees are additional. Top-ups move free cash into that side's escrow and
remain possible during oracle staleness. Removing margin and withdrawing
unrealized profit are not implemented. Free cash withdrawals cannot take escrow
or open-order reservations. Partial reductions realize PnL on the closed slice.

Mark changes affect unrealized equity, not actual collateral. The pair's equity
sum equals posted escrow. Negative equity is recognized as a deficit rather
than minted into a withdrawable balance.

## Funding

Funding requires the next consecutive epoch, a fresh snapshot, and at least
the configured interval since the previous funding event, starting at time zero.
It applies to every currently open pair. A positive rate makes long pay short;
a negative rate makes short pay long. It uses the index, separately from mark:

```
magnitude = floor(q * index_price * abs(rate_bps) / 10000)
long payment = sign(rate_bps) * magnitude
short payment = -long payment
```

The pair stores the cumulative long payment. Dust rounds toward zero
symmetrically. Funding is included in final payouts; it neither creates
protocol income nor changes total collateral. Every pair must meet maintenance
before applying the event; otherwise the entire event rejects until those
pairs are resolved. Funding can cause a pair to fall below maintenance afterward.

This is discrete snapshot funding: no holding-time proration, automatic
scheduler, or multiplication of late events by missed intervals. The supplied
rate is not derived from a premium or authenticated history. A real schedule
and downtime-recovery policy are still required before serving traders.

## Closing, liquidation and bankruptcy

Either labeled counterparty can request full pair close. The local operator
can liquidate only if one side is strictly below maintenance. There are no
owner signatures, liquidation bounties, penalties or partial liquidations.
Voluntary partial reductions must preserve both residual sides' maintenance.
Settlement closes the healthy counterparty as well, at a mock
mark. It does not establish executable liquidity or timely keeper behavior.

1. Compute raw equity including cumulative funding.
2. A negative side creates a deficit. Independently funded simulated insurance
   covers at most its available balance.
3. The positive side receives at most actual escrow plus insurance used.
   Any uncovered amount is an explicit payout haircut.
4. Deduct exit fees capped at gross payouts; credit cash, debit insurance,
   record uncovered loss, and close the pair atomically.

Example: q=10, e=10,000, mL=mS=20,000, F=0 and p=15,000 produces raw equities
of 70,000 and -30,000. Insurance of 10,000 allows a 50,000 long payout before
fees, with a disclosed 20,000 haircut. The short's other cash stays isolated.

`uncovered_loss` records cumulative realized payout shortfalls. The haircut
extinguishes the corresponding modeled claim; this is not an IOU or promise
of recovery. Treasury release, depositor guarantees, automatic deleveraging
and cross-pair socialization are not implemented. Conservation does not imply
that positions are profitable or fully recoverable.

## Accounting and receipts

After every mutation:

```
net simulated deposits (including insurance) - simulated withdrawals
  = free cash + open-order reserves + open-pair escrow + insurance + protocol fees
```

Each receipt commits to the ordered perpetual account, order, pair, funding log,
oracle, risk configuration and metadata tables. Before the next mutation and
during audit, the latest commitment must match the actual tables. This detects
value-conserving edits, such as changed accumulated funding, as well as balance
inconsistencies. Receipts are linked consecutively by their hashes.

There are no signatures, external checkpoints or Kaspa anchors. An administrator
can rewrite all records and recompute the hashes or truncate history. These
checks establish local consistency, not external truth or ownership.

## Run it

```bash
python -m static_protocol perps-demo
python -m static_protocol serve --db static-sim.sqlite
python -m static_protocol perps-audit --db static-sim.sqlite
```

`examples/perps-output.json` contains the deterministic demo: opening, funding,
margin top-up, liquidation and final audit. See [API.md](API.md) for HTTP routes.

## Next work

Define price/rate authentication, funding scheduling, price discovery,
and executable liquidation slippage. Compiled covenant validation is a
separate milestone. This simulator enables no live settlement path.
