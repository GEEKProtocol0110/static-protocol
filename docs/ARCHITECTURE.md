# Architecture and accounting

## Boundaries

The executable reference has a pure auction function, a transactional local
ledger, an operator API, and independent economic models. The settlement ledger
is an off-chain database and is controlled by the local operator. Database rows
model intent buffers; they are not Kaspa UTXOs or consensus-enforced covenants.
Python is used for an inspectable reference model, not as a production
high-frequency execution or covenant runtime.

## Intake state

An order has owner, nonce, side, quantity, remaining quantity, integer limit
price, DAA expiry, and fee rail. An order ID is SHA-256 over a domain-separated
canonical intent, including the single market identifier. Retrying the same
intent returns its existing record. A changed intent with the same owner/nonce
is rejected even after cancellation or fill.

An order transitions from `open` to `filled`, `cancelled`, or `expired`. Partial
fills leave it `open` with reduced remaining quantity and reservation. SQLite's
`BEGIN IMMEDIATE` serializes state-changing operations; it does not model
contention-free execution on a distributed ledger.

For a buy of q lots at limit p and fee rail r:

```
reserve_quote(q,p,r) = q * (p + ceil(p * rate(r)))
```

For sells, reserve exactly q base lots. The deliberately conservative buy
reservation covers ceil rounding for any future partition into partial fills.
Reject overflow before mutation; reject any change that would make available
or locked balance negative. Simulated withdrawals spend available balance only.

## Batch admission and clearing

Each sweep rejects a backward DAA, expires overdue orders and refunds their
locks, then selects up to 80 open orders using a persistent rotating cursor.
Rotation prevents an unfilled first page from permanently starving later
orders. Expiry processing is local bookkeeping and may affect more than 80
orders; it is not represented as a single bounded on-chain transaction.

For the fixed admitted set, evaluate every distinct limit price as a candidate
p. Compute demand at limits >= p and supply at limits <= p. Choose:

1. Maximum executable volume `min(demand, supply)`.
2. Minimum absolute demand/supply imbalance among equal-volume candidates.
3. Lowest candidate tick for any remaining tie.

Allocate the resulting volume separately to each side in price priority and
then order-ID order. Every filled order executes at that one price, within its
limit. These choices are reference policies that require protocol review.
They are not the whitepaper's volume-weighted average of submitted limits.

The clearing result is permutation-invariant for an already fixed batch. Batch
membership, expiry advancement, cancellations, repeated small batches, and
owner-selected nonces remain manipulation surfaces. No MEV proof is claimed.

## Settlement

For a fill q at p, let n=q*p and f=ceil(n*rate). The buyer pays n+f quote atoms
and receives q base lots; the seller releases q locked lots and receives n-f
quote atoms. Each order's residual reservation is recomputed and surplus is
returned to available balance. Both fees enter the separate quote fee account.

All balance changes, order changes, DAA/cursor changes, and receipt creation
commit in one database transaction. Failure rolls the entire sweep back. The
same sweep request ID and parameters return the original receipt, including
after a restart. Reusing the ID with different parameters is rejected.

After every ledger mutation, for each asset independently:

```
sum(available + locked) + protocol_fees = simulated_deposits - simulated_withdrawals
```

For each owner and asset, locked balance equals the reservations of that
owner's open orders in that asset. These are local conservation checks. They
are not an externally complete proof of solvency or of owned real assets.

## Receipts

Receipt payloads contain the market, sequence, DAA, sweep request hash, admitted
order IDs, expired order IDs, fills, price, fees, and previous receipt hash.
`audit()` checks the hash links and local accounting. The request ID is linked
to its parameters. Receipt encoding uses canonical sorted JSON with integer
monetary fields.

This chain detects uncoordinated edits. An administrator able to replace the
database can rewrite the ledger and recompute every hash or truncate history.
There are no signatures, external checkpoints, Merkle inclusion proofs, or
Kaspa anchors in v0.1.0. Preserve an independently witnessed head before
asserting any tamper resistance beyond local consistency.

## Separate reserve and treasury models

The chosen curve is `f(s)=a+b*s`, integrated using exact rational arithmetic.
Buys round the integral upward; sells round it downward. Reserve coverage is
checked against the integral of all outstanding supply. Any rounding dust
stays in reserve. The model has no owner balances or transferable token ledger.

The per-transaction issuance cap is `max_supply // Fibonacci_divisor`, where
the divisor is 144, 233, or 377. This intentionally uses configured maximum
supply to avoid the whitepaper's zero-supply bootstrap ambiguity. Repeated
transactions remain possible. Crossing a reserve threshold only sets a
readable signal; there is no automatic AMM graduation.

The independent AMM uses ceil division when deriving the new output reserve,
so the product of reserves cannot decrease through swap rounding. There are
no fees or LP claims in that model.

Treasury units are simulated sompi. A four-atom allocation cycle gives 50/25/25
without negative rounding adjustments. OpEx entitlement is a linear fraction
of 5% of the balance fixed at epoch opening, capped at 30 days, less already
spent. New margin does not inflate that epoch's allowance. Starting a new epoch
early or moving model time backward is rejected. Models are mutable Python
objects for analysis; no administrative access constraint is chain-enforced.

The sovereign release method always rejects. No caller-provided boolean,
balance snapshot, or JSON object is accepted as a cryptographic insolvency
proof. Backstop loss absorption, asset conversion, completeness proofs,
auction pricing, and actual depositor recapitalization remain unimplemented.
