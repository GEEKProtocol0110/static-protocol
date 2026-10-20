# Changelog

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
