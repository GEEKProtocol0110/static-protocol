# Contributing to STATIC

STATIC is an executable research specification for settlement on Kaspa.
Contributions should improve the reference model or provide verifiable steps
toward covenant integration. The current release uses simulated assets only.

## Local setup

Use Python 3.11 or later. The runtime and test suite use the standard library.

```bash
git clone https://github.com/GEEKProtocol0110/static-protocol.git
cd static-protocol
python -m static_protocol demo
python -m unittest discover -s tests -v
```

Create a focused branch for your change. No database, operator token, wallet
secret, private key, environment file, or machine-specific path belongs in a
commit. Tests should use temporary databases and fictional balances.

## Before changing behavior

Read [architecture](docs/ARCHITECTURE.md), [implementation decisions](docs/SPEC_REVIEW.md),
and [project status](docs/STATUS.md). For economic changes, identify the units,
rounding rule, conservation invariant, and behavior under partial execution.
Explain why the previous behavior was wrong or incomplete.

The pure auction must stay deterministic for a fixed batch. Ledger mutations
must remain atomic and conserve each asset separately. Persistent request IDs
and nonces must continue to protect the operations that currently support
retries. Keep all live adapter paths disabled until there is real validation
evidence for their implementation.

## Pull requests

Keep the change focused. Describe the concrete trigger and resulting behavior;
include meaningful tests for changed accounting, state transitions, or security
rules. Run the full standard-library suite when changing the engine. Update
the relevant documentation and changelog when public behavior changes.

For documentation-only changes, check links and rendered formatting; additional
unit tests are unnecessary. For covenant work, state the exact compiler/node
versions, network, source and artifact hashes, and which positive and negative
transaction vectors actually ran. Label proposed or uncompiled code clearly.

## Useful first contributions

- Reproduce an accounting or auction edge case with a small failing example.
- Review the admission and tie-breaking policies for manipulation incentives.
- Improve the clarity of units and state-transition specifications.
- Add a narrow, compiled intake-covenant proof of concept against a pinned
  toolchain, with tests showing which spends are accepted and rejected.

Use issue forms for ordinary bugs and proposals. For a security concern,
follow [SECURITY.md](SECURITY.md) and avoid posting sensitive material publicly.
