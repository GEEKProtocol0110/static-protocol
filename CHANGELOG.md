# Changelog

## 0.3.0 — Perpetual orders and partial reductions

- Collateralized long/short limit intents with immutable nonce protection,
  cancellation, expiry, and fee coverage across arbitrary partial fills.
- Rotating admission of up to 80 orders, deterministic price/time priority
  within each admitted set, and limit-protected matching at a fresh mock mark.
- Partial pair reductions with exact realized PnL, proportional margin/funding
  allocation, retained rounding dust and residual maintenance checks.
- Verified schema migration preserving all v0.2 receipts and collateral.
- Four HTTP routes, an orders demo, worked output and a matching specification.
- 37 additional tests (126 total), including migration, many small fills,
  concurrent reservations, injected rollback and unsafe rounding cases.

Matching uses an operator-supplied mark. Both paired counterparties reduce
together. Authenticated prices, price discovery, executable liquidation and
live settlement remain future work.

## 0.2.0 — Paired perpetuals simulator

### Added

- Persistent matched long/short pairs with isolated quote collateral, entry and
  exit fees, initial/maintenance margin, unrealized PnL and margin top-ups.
- Mock index/mark observations with freshness, divergence and time bounds.
- Sequential funding events with symmetric rounding and no protocol revenue.
- Full-pair close and liquidation accounting, finite insurance coverage, and
  explicitly disclosed uncovered-loss haircuts.
- Idempotent mutations, receipt chains and complete perpetual state commitments.
- Local perpetual HTTP routes, CLI demo/audit, and a documented risk model.
- 41 additional tests (89 total), including the full HTTP funding/liquidation
  flow, concurrent retries, restart, tampering and price-gap shortfalls.

### Scope

Pairs are supplied by the operator and settle at mock marks. Limit-price
matching, partial reductions, real oracle authentication, executable liquidation
and live settlement remain future work. Spot collateral stays separate.

## 0.1.0 — Reference implementation

### Added

- Deterministic uniform-price call auction with maximum-volume clearing,
  partial fills, limit protection, and stable tie-breaking.
- SQLite order ledger with collateral locks, simulated funding, cancellation,
  DAA expiry, rotating batch admission, and account-level self-trade guard.
- Durable nonce and sweep retry protection, hash-linked receipts, conservation
  checks, and local audit commands.
- Integer fee schedules at 0.050% and 0.025%, with explicit rounding behavior.
- Independent linear reserve, constant-product AMM, and 50/25/25 treasury
  models, including bounded OpEx release.
- Loopback operator API, worked examples, and 48 automated checks.
- Architecture, whitepaper review, threat model, validation records, and a
  staged covenant-integration plan.
- Public repository presentation, contributor guide, issue forms, PR template,
  and Python 3.11/3.12/3.13 CI configuration.

### Scope

Simulated assets and local accounting only. Live broadcasting, foreign
collateral conversion, protocol-fee conversion, automatic AMM graduation, and
sovereign reserve release are disabled pending verifiable implementations.
