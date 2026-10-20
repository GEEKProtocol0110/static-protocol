# Project checkpoint

Version: 0.1.0

## Completed

- Read and mapped all four pages of the supplied whitepaper.
- Implemented a deterministic local spot auction and transactional ledger.
- Implemented durable order and sweep retries, cancellation, expiry,
  rotating bounded intake, account-level self-trade prevention and receipts.
- Implemented independent reserve, constant-product AMM, and treasury models.
- Added a local operator API and complete HTTP walkthrough.
- Added automated tests for accounting, random books, partial-fill rounding,
  concurrency, injected faults, restart recovery, tampering, slippage and
  treasury limits. See VALIDATION.txt for the executed test results.
- Included CI configuration, source observations, design decisions and handoff.

## Next milestone: one verifiable Kaspa intake buffer

1. Select and pin an exact SilverScript compiler commit/release and rusty-kaspa
   node/SDK commit. Record network and activation/feature flags from that node.
   This package does not claim to have observed the chain's activation state.
2. Implement a minimal intake covenant with owner signature, spend/cancel
   authorization, expiry, immutable intent commitment, and replay boundaries.
3. Compile it; record source, constructor arguments, artifact hash, script
   bytes, test vectors, and actual mass/compute limits. No placeholder script
   should be presented as compiled or deployable.
4. Validate valid and adversarial spends in the node's script/consensus tests.
   Include changed owner, wrong asset, changed amount, invalid expiry,
   duplicate spend, forged lineage, missing output and excessive fee.
5. Construct and decode a transaction locally, then perform an explicitly
   identified testnet create/cancel cycle with receipts and independent RPC
   confirmation. Never infer broadcast success from a local receipt hash.

## Following milestones

| Order | Deliverable | Exit evidence |
| --- | --- | --- |
| 2 | Small-batch settlement covenant | Reference-vector agreement; value conservation; exact mass/compute; conflict/reorg tests |
| 3 | Persistent reserve vault | Actual asset lineage, authenticated mint/burn, full supply/reserve accounting |
| 4 | Treasury contracts | Epoch commitments, true native conversion route, complete solvency proof and crisis state machine |
| 5 | Graduation transition | Explicit AMM seeding; price continuity; no creator escape path; valid redemption semantics |
| 6 | User application and wallet flow | Signed intents, readable receipts, scoped permissions, no seed entry |
| 7 | External integration and launch readiness | Reviewed bridge/oracle/stablecoin model; independent audits; bounded operational controls |

Perpetuals, cross-chain collateral and broad production access follow a separate
risk specification. They are not hidden launch dependencies for this first
spot reference engine.

## Handoff

Run the tests, then `python -m static_protocol demo`. Read SPEC_REVIEW.md before
adding integrations. Keep `LiveSettlementAdapter` fail-closed until each live
implementation has actual chain validation evidence. Carry new invariants into
both the executable reference tests and the future covenant test suite.

The source repository is https://github.com/GEEKProtocol0110/static-protocol.
The project remains a local reference implementation; no network deployment
or live-fund integration has been completed.
