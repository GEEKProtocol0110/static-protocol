# Local operator API

Base URL: `http://127.0.0.1:8787`. All routes except `/health` require
`Authorization: Bearer <operator-token>`. POST requests require
`Content-Type: application/json` and a body of 1-65536 bytes. Monetary values
must be integers, not decimal strings or floats. Unknown fields are rejected.

This is a simulator's administrative interface. `owner` is a label supplied
by the operator; it is not an authenticated wallet. Never expose it as an
internet-facing exchange API. No CORS access is enabled.

| Method | Route | Body / behavior |
| --- | --- | --- |
| GET | `/health` | Public mode/version, explicitly reports no live funds |
| GET | `/v1/state` | Full local balances, orders, fees and receipts |
| GET | `/v1/audit` | Conservation, lock accounting and receipt-chain checks |
| POST | `/v1/funding` | `owner`, `asset`, `amount`, optional `direction`: `in` or `out` |
| POST | `/v1/orders` | `owner`, `nonce`, `side`, `quantity`, `limit_price`, `expiry_daa`, optional `fee_rail` |
| POST | `/v1/cancel` | `owner`, `order_id` |
| POST | `/v1/sweeps` | `request_id`, `daa`, optional `max_inputs` (1-80, default 80) |

Funding accepts `STATIC_TEST` and `USD_TEST` only; it creates/removes fictional
balances. `side` is `buy` or `sell`. `fee_rail` is `native` or `foreign`, with
`foreign` as default. Owner, nonce and request ID are 1-96 ASCII letters,
digits, underscores, periods, colons or hyphens.

Example order body:

```json
{
  "owner": "alice",
  "nonce": "order-1",
  "side": "buy",
  "quantity": 10,
  "limit_price": 110,
  "expiry_daa": 100,
  "fee_rail": "native"
}
```

Success returns status 200. Expected invalid POST input/state returns 400;
unauthorized access returns 401; unknown route returns 404; audit-detected
invariant failures return 409. Unexpected POST failures return a generic 500.
Sweep IDs provide durable retry protection. Funding operations do not have
idempotency keys; retrying one credits/debits again. The example uses distinct
account names for repeatable runs. Read endpoints return the full dataset;
pagination, rate limiting and public-service hardening are outside this build.

## Perpetuals simulator (v0.3.0)

The same token authorizes these routes. Perpetual `USD_TEST` collateral is
separate from spot balances. Each mutation requires a unique `request_id` and
integer simulation `now`. Identical retries return the original receipt;
changing parameters with the same ID is rejected. Perpetual account funding
is retry-safe, unlike the original spot funding endpoint.

| Method | Route | Body |
| --- | --- | --- |
| GET | `/v1/perps/state` | Full perpetual state and receipts |
| GET | `/v1/perps/audit` | Conservation, receipt chain and state commitment |
| POST | `/v1/perps/accounts/funding` | `request_id`, `owner`, `amount`, `now`; optional `direction` (`in` default or `out`) |
| POST | `/v1/perps/insurance` | `request_id`, `amount`, `now` |
| POST | `/v1/perps/oracle` | `request_id`, `index_price`, `mark_price`, `observed_at`, `now` |
| POST | `/v1/perps/open` | `request_id`, `long_owner`, `short_owner`, `quantity`, `long_margin`, `short_margin`, `now`; optional `long_rail`/`short_rail` |
| POST | `/v1/perps/orders` | `request_id`, `owner`, `nonce`, `side`, `quantity`, `limit_price`, `margin_per_lot`, `expires_at`, `now`; optional `fee_rail` |
| POST | `/v1/perps/orders/cancel` | `request_id`, `order_id`, `owner`, `now` |
| POST | `/v1/perps/match` | `request_id`, `now`; optional `max_inputs` (1-80, default 80) |
| POST | `/v1/perps/top-up` | `request_id`, `pair_id`, `owner`, `amount`, `now` |
| POST | `/v1/perps/funding` | `request_id`, `epoch`, `rate_bps`, `now` |
| POST | `/v1/perps/close` | `request_id`, `pair_id`, `owner`, `now` |
| POST | `/v1/perps/reduce` | `request_id`, `pair_id`, `owner`, `quantity`, `now` |
| POST | `/v1/perps/liquidate` | `request_id`, `pair_id`, `now` |
| POST | `/v1/perps/risk` | `pair_id`, `now`; read-only risk query |

Rails default to `foreign`. Before opening, fund both perpetual accounts and
publish a fresh mock oracle observation. Read `pair_id` from the opening
receipt's `result`. Funding epochs advance by one and respect the configured
minimum interval. Liquidation closes both paired positions.

Example opening after deposits and a mark of 10,000:

```json
{
  "request_id": "open-1",
  "long_owner": "alice",
  "short_owner": "bob",
  "quantity": 10,
  "long_margin": 20000,
  "short_margin": 20000,
  "now": 0
}
```

Receipts include `kind`, `now`, `request_hash`, `config_hash`, `state_hash`,
`previous_hash`, and the transition's `result`. Liquidation results disclose
equity, maintenance, insurance, uncovered loss, each haircut, fees and payouts.
See [PERPETUALS.md](PERPETUALS.md) for full assumptions and limitations.

Perpetual order `side` is `long` or `short`. Placement reserves margin and
worst-case fees without requiring a fresh price. `expires_at` is integer
simulation time, independent of spot DAA. Matching requires a fresh mock mark;
each receipt lists admitted IDs and the resulting pair IDs. A short's collateral
also imposes a maximum fill price. Cancellation refunds pending reserves.

Example intent after depositing collateral:

```json
{
  "request_id": "intent-1", "owner": "alice", "nonce": "long-1",
  "side": "long", "quantity": 5, "limit_price": 10100,
  "margin_per_lot": 3000, "expires_at": 7200, "now": 0
}
```

Place a separately funded short, publish a fresh mark of 10,000, then call
`/v1/perps/match` with `{"request_id":"match-1","now":0}`. Reduce a returned
pair with `{"request_id":"reduce-1","pair_id":"<pair_id>","owner":"alice",
"quantity":1,"now":0}`. This reduces both counterparties. A reduction of the
entire current quantity uses full-close insurance and haircut rules; a smaller
slice must leave both sides safe. See
[ORDERS-AND-REDUCTIONS.md](ORDERS-AND-REDUCTIONS.md).
