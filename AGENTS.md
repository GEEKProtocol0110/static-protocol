# Working on STATIC

Read README.md, docs/STATUS.md, docs/ARCHITECTURE.md, and docs/SPEC_REVIEW.md
before changing protocol behavior. This repository is an executable reference
model; simulation behavior is not consensus or custody enforcement.

- Keep units explicit and monetary values integer-only. Conserve each asset
  separately. Account for worst-case fee rounding across partial fills.
- Keep ledger mutations atomic. Preserve nonce and sweep retry semantics.
- Preserve deterministic results for a fixed admitted batch. Explain changes
  to selection, tie-breaking, expiry, fees, reserves, or solvency policy.
- Use meaningful tests for changed invariants and failure cases. Run
  `python -m unittest discover -s tests -v` for engine changes and
  `python -m static_protocol demo` for changes affecting the walkthrough.
- Keep databases, tokens, secrets, private keys, and private user data out of
  source control. Local examples must use fictional assets.
- Do not enable live adapters with a flag or accept caller assertions as
  cryptographic evidence. Document and validate the actual verification path.
- Covenant integrations must record exact compiler/node versions and actual
  compilation and validation results. Do not call a local hash a chain receipt.
- Update docs/STATUS.md and CHANGELOG.md when implementation status changes.
- BUILD.json lists SHA-256 content hashes. Refresh it after changing tracked
  project files, excluding BUILD.json itself and Git/cache/runtime files.

Read docs/PERPETUALS.md before editing the paired perpetual simulator. Its
collateral ledger is separate from spot and its close/liquidation path settles
both counterparties at a mock mark. Preserve funding neutrality, finite
insurance, explicit loss haircuts and state commitments. Never hide bad debt
by minting a cash balance or silently taking unrelated isolated collateral.

The next perpetuals milestone is matching and partial reductions; intake-covenant
validation remains a separate chain milestone in docs/STATUS.md. Do not present
live derivatives, foreign collateral, graduation, non-custody, throughput targets,
or MEV immunity as completed features.
