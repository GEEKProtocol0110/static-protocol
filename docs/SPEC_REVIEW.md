# Whitepaper review and implementation decisions

Source: supplied STATIC Protocol Technical Whitepaper v1.0, four pages.
This document separates the proposal's intent from properties the build can
actually establish. No comparison claim about another exchange was needed to
implement the reference engine.

| Proposal | Missing definition or limiting assumption | v0.1.0 decision |
| --- | --- | --- |
| Isolated intake removes contention | Settlement still consumes shared state; admission has spam/mass cost | Model isolated intents and atomic serial settlement; do not claim distributed concurrency |
| Sweeps of 50-80 inputs | Input count alone does not determine mass, script compute, signatures, or feasibility | Cap modeled buffers at 80; allow small local batches; no network capacity claim |
| VWAP nullifies MEV | Limit prices are not independently executed prices; no allocation rule, batch cutoff, inclusion guarantee, or manipulation proof | Define a maximum-volume uniform-price call auction; retain batch inclusion/grinding as explicit risks |
| Foreign collateral becomes rollback-proof | Bridge verification, finality, swap counterparties, stable issuer/depeg, and transferred losses remain | Disable conversion until each trust and finality boundary is specified and tested |
| USD purchasing power is deterministic | Token quantity does not establish redemption value or purchasing power | Use a fictional USD_TEST asset without a peg assertion |
| Native rail earns 50% off taker fees | Eligibility proof, mixed rails, maker definition, rounding, gas reimbursement unspecified | Two operator-selected fee schedules; fee both sides; disclose rounding and lack of vault proof |
| Fuel jar can activate native eligibility | Ownership, native balance threshold, address linking, and withdrawals unspecified | Not implemented |
| Curve always offers reserve equity | Curve function, fees, reserve ownership and depletion rules unspecified | Choose an exact linear integral model with conservative rounding |
| Fibonacci divisions stop sniping | Current supply starts at zero; division restricts one transaction, not a Sybil's aggregate purchases | Use max-supply fraction only as a per-transaction cap; no anti-Sybil claim |
| Atomic AMM graduation locks liquidity | Token reserve seeding, curve/AMM price continuity, leftover tokens, and swap redemption transition unspecified | Threshold signal and AMM arithmetic remain independent; graduation adapter rejects |
| 50/25/25 from net margin in KAS | Gross vs net, oracle, slippage, FX execution and atomicity unspecified | Keep exchange quote fees in quote; test separate simulated-sompi split |
| 5% OpEx drip per 30 days | Opening balance, cumulative vs per-call allowance, epoch transitions unspecified | Linear allowance on fixed epoch-opening balance; cumulative spend cap |
| Reserve releases only on insolvency proof | Complete liabilities/assets set, external valuations, proof verification, replay, and release schedule unspecified | Reject all sovereign withdrawal requests |
| Same input/output script preserves lineage | State changes may change script bytes; equality can conflict with mutable state or AMM graduation | Require explicit state/lineage commitment design before writing covenant code |
| Assets >= liabilities in all states | Preventing every deficit differs from supporting an already-insolvent state | Local conservation now; crisis state machine and risk model remain gates |
| Sub-50ms, 100+ BPS, perpetual miner funding | End-to-end confirmation/latency model, benchmark, fee schedule, demand and security assumptions absent | No latency, throughput, demand, or perpetual-security claim |

## Decisions still required for a real financial protocol

1. Choose spot-only launch versus derivatives. Perpetuals require positions,
   margin, funding, oracle freshness, liquidation, bankruptcies, and loss
   socialization rules; none is implemented here.
2. Define the asset representation and its relationship to covenant IDs,
   asset lineage, KCC20 or another token system. A string is not an asset proof.
3. Define which signatures authorize which intent, including chain/domain
   separation, owner changes, cancellation, nonce policy, and wallet support.
4. Define batch windows and admission completeness/fairness. A sweeper should
   not be able to improve its outcome by excluding valid competing orders.
5. Specify oracle and bridge trust, finality, emergency behavior and stablecoin
   redemption. Native settlement cannot prove truth about an external event
   without an explicit verification mechanism and assumptions.
6. Specify treasury proof completeness and a dimensional valuation model.
   Sompi and USD units cannot be subtracted without a verified conversion rule.
7. Measure actual transaction mass/compute and shared-state behavior against
   an exact node/compiler build before selecting input counts or capacity.

## How to use this build

Treat the engine and its tests as an executable candidate specification.
Port one narrow invariant to compiled covenant code, prove agreement with
the reference vectors, then test it against the node's validation engine.
Do not promote local database checks into consensus or non-custody claims.
