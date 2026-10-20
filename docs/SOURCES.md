# Source observations

Reviewed 2026-10-20 UTC. Links are primary sources; upstream content can change.
These observations establish a development path, not this project's deployed
compatibility or any network's current activation state.

## Supplied specification

`STATIC-Whitepaper-v1.0.pdf` is a byte-identical copy of the supplied technical
whitepaper. Its proposal is the input to the reference build. Claims about
other networks, throughput, finality and economics have not been adopted as
measured facts.

## Kaspa development sources

- https://github.com/kaspanet/silverscript
  Official compiler repository. Its README describes a Rust workspace,
  `silverscript-lang`, a compiler targeting Kaspa Script, compilation via
  `cargo run -p silverscript-lang --bin silverc -- contract.sil`, constructor
  argument support, examples, tutorials and covenant declaration docs.
  The retrieved README references rusty-kaspa 2.1.0 crates. An exact commit
  still needs to be pinned before compiling this project's first covenant.
- https://github.com/kaspanet/silverscript/releases
  Official compiler release channel. Search retrieval reports v1.0.0;
  no release artifact was installed, compiled, or validated in this build.
- https://github.com/kaspanet/kips
  Official improvement proposal repository. The repository was located, but
  individual proposal revisions were not used as a compiled API contract.

No third-party description was used to assert Toccata activation, current
mainnet covenant support, a native stablecoin, a throughput number, or a
deployable transaction format. Verify all against a pinned node before use.

## GitHub CI tooling

- https://github.com/actions/checkout
- https://github.com/actions/setup-python

The official READMEs currently show v7 usage for both actions. The workflow
uses those major versions, read-only repository permission, an explicit
Ubuntu 24.04 runner, and no persisted checkout credentials.
