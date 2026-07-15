# Changelog

All notable changes to xchain-sdk are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-15

### Added
- Initial release of xchain-sdk
- `XChainClient` high-level API for cross-chain certificate management
- `ChainEndpoint` abstraction for EVM-compatible chains
- `ChainManager` for running multiple local EVM nodes (py-evm backend)
- CLI tool with subcommands: `start`, `stop`, `status`, `deploy`, `mint`, `bridge`, `info`, `demo`, `explorer`
- Smart contracts: `LiquorCertificate`, `LiquorPledge`, `CrossChainBridge`
- 5 verification dimensions in CrossChainBridge: asset mapping, Merkle proof, identity, replay protection, finality
- Cross-chain relayer (`xchain.relayer`)
- Python `bridge_core` library for message hash, encoding, and Merkle helpers
- Test suite (`pytest`)
- Documentation: README, quickstart, architecture, API reference
- GitHub Actions CI for lint + test + build
