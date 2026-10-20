# Security boundary

STATIC v0.2.0 is a local simulator. Use fictional balances only. Its checks
are useful for verifying candidate economics and state transitions, not for
custody or public exchange operation.

## Reporting a concern

Do not post secrets, exploit credentials, wallet material, or private user data
in public issues. Use GitHub's private vulnerability reporting if it is enabled
for this repository. Otherwise, ask the maintainer to arrange a private channel
before sharing sensitive reproduction details. Ordinary fictional-asset
accounting bugs can use the bug report form.

## Trust assumptions

- The local operator controls simulated identities, deposits, fee eligibility,
  DAA advancement and batch invocation. They can edit the database directly.
- The local API token grants all operator powers, not per-account access.
- SQLite provides local transaction atomicity and durability on a trustworthy
  filesystem. It is not consensus, replication, a permissionless sequencer,
  a disaster recovery strategy, or high-frequency exchange infrastructure.
- Receipt hashing has no signatures or external anchor. Full database rewrite,
  deletion/truncation and coordinated rehashing cannot be ruled out.
- Pure economic models are in-memory mutable objects. Their reserve/supply
  bookkeeping is not an authenticated user token or deposit ledger.

## Checks that are implemented

Balances cannot become negative through valid API transitions. Funds locked
for orders cannot be withdrawn. Orders cannot reserve nonexistent simulated
funds. Nonces cannot be repurposed. Sweeps are retry-safe and transactional.
Expired orders cannot execute. Partial-fill fee rounding is reserved in
advance. Uniform price fills respect submitted limits. Net per-asset funding
equals balances plus accrued fees after each mutation. Inconsistent local
receipt chains are detected. Same-name opposite-side open orders are blocked.

## Remaining adversarial surfaces

Batch inclusion/exclusion, batch timing, cancellation timing, multiple owner
labels, nonce grinding, liquidity concentration and strategic limit placement
are not solved by uniform pricing. Native fee eligibility is not verified.
There are no cryptographic owner signatures. There is no bridge, authenticated
oracle, stablecoin issuer verification, live leveraged trading, gas conversion,
distributed shared-state enforcement or consensus script.

The paired perpetuals simulator supports isolated margin, funding and modeled
liquidation. Its operator controls identities, matched counterparties, time,
prices and rates. Mark/index bounds cannot authenticate a jointly forged feed.
Both counterparties close at a mock mark without execution or slippage proof.
Funding has no premium estimator or scheduler. Its finite insurance can run
out; receipts explicitly record payout haircuts. Unrelated isolated cash is
not seized to hide those losses. See docs/PERPETUALS.md for the exact rules.

Perpetual receipts also commit to current state, catching uncoordinated
value-conserving edits such as a changed funding accumulator. An administrator
can still rewrite the entire database and recompute all hashes or truncate
history. No external solvency proof or custody enforcement is established.

The loopback API is intended for trusted local use. It is not hardened against
malicious local users or unbounded request concurrency. Full-state reads and
invariant scans grow with database size. Funding has no retry key. Do not
tunnel, proxy or rebind the API to a public interface.

The reserve override, foreign conversion, fee conversion, automatic graduation
and live broadcasting are explicit rejecting stubs. Enabling them requires
implemented verification rules and tests; a configuration flag is insufficient.

## Validation limits

Passing tests does not establish an absence of economic attacks, capacity
under load, chain compatibility, non-custody, formal verification or fitness
for funds. Before any fund-bearing deployment, compile and validate real
covenants, test node behavior and reorgs, define complete asset/liability and
oracle assumptions, and obtain independent security review of that exact build.
