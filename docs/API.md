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
