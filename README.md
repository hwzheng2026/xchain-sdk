# xchain-sdk

> Cross-chain middleware SDK for FISCO BCOS ↔ ChainMaker (and any EVM-compatible chain).

[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0-green)](CHANGELOG.md)

A reusable Python SDK and CLI for building cross-chain applications on top of EVM-compatible consortium chains. Ships with a working demo: digital liquor certificates (酒证) bridged between a FISCO BCOS-style chain and a ChainMaker-style chain.

## Features

- 🔗 **Cross-chain bridge**: lock-and-mint style asset bridging between two EVM chains
- 📜 **5 verification dimensions**: asset mapping consistency, Merkle event traceability, cross-chain identity, replay protection, finality confirmation
- 🛠 **High-level SDK**: `XChainClient` Python API
- 🖥 **CLI**: `xchain start/deploy/mint/bridge/info/demo` for the full lifecycle
- 🧪 **Local dev chains**: `py-evm` + `eth-tester` backend (no Docker, no Java required)
- 🔌 **Pluggable**: works with any EVM-compatible chain (FISCO BCOS, ChainMaker, Ethereum, Polygon, etc.)

## Quickstart

### Install

```bash
pip install xchain-sdk
```

Or from source:
```bash
git clone https://github.com/yourorg/xchain-sdk
cd xchain-sdk
pip install -e .
```

### Use the CLI

```bash
# Start two local dev chains (BCOS :8545, ChainMaker :8546)
xchain start

# Deploy the 3 contracts on both chains
xchain deploy

# Mint a certificate on BCOS
xchain mint 0x$(python3 -c "import secrets; print(secrets.token_hex(32))") \
  --warehouse GZJJ-2024-001 --vintage 2024 --valuation 500000 \
  --holder 0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf

# Bridge it to ChainMaker
xchain bridge <cert_id> --warehouse GZJJ-2024-001 --vintage 2024 --valuation 500000

# Run the full demo
xchain demo

# Inspect a certificate on both chains
xchain info <cert_id>

# Show live status
xchain status
```

### Use the Python SDK

```python
from xchain import XChainClient, ChainEndpoint

bcos = ChainEndpoint("bcos", "http://127.0.0.1:8545", chain_id=1001)
cm = ChainEndpoint("chainmaker", "http://127.0.0.1:8546", chain_id=2001)

client = XChainClient(
    source=bcos, target=cm,
    source_contracts={"LiquorCertificate": "0x...", "LiquorPledge": "0x...", "CrossChainBridge": "0x..."},
    target_contracts={"LiquorCertificate": "0x...", "LiquorPledge": "0x...", "CrossChainBridge": "0x..."},
    deployer="0x...",
)

# Mint a certificate
result = client.mint_on_source(
    cert_id="0x...",
    warehouse_code="GZJJ-2024-001",
    vintage=2024,
    valuation=500000,
    holder="0x...",
)

# Bridge it
bridge_result = client.bridge_mint(
    cert_id="0x...",
    target_recipient="0x...",
    warehouse_code="GZJJ-2024-001",
    vintage=2024,
    valuation=500000,
)
print(f"Mirror cert: {bridge_result['targetCertId']}")

# Read the mirror
mirror = client.get_target_certificate(bridge_result['targetCertId'])
print(mirror)
```

## Architecture

```
              ┌─────────────────┐                  ┌─────────────────┐
              │   BCOS chain    │                  │ ChainMaker chain│
              │   (chain 1001)  │                  │   (chain 2001)  │
              │                 │                  │                 │
              │ LiquorCert      │                  │ LiquorCert      │
              │ LiquorPledge    │                  │ LiquorPledge    │
              │ CrossChainBridge├──── relayer ────►│ CrossChainBridge│
              │                 │  (HTTP/gRPC)     │                 │
              └────────▲────────┘                  └────────▲────────┘
                       │                                    │
                       └──── xchain-sdk Python API ────────┘
                            + CLI + relayer + tests
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details on the cross-chain protocol, message format, and Merkle proof scheme.

## The 5 Verification Dimensions

The `CrossChainBridge` contract enforces 5 dimensions of security for every cross-chain message:

1. **Asset mapping consistency** — `sourceToTarget[sourceCertId]` and `targetToSource[targetCertId]` must agree; a source cert can only mint one mirror.
2. **Merkle event traceability** — every message hash must be included in the source chain's event tree; verified via `verifyMerkleProof()` on the target chain.
3. **Cross-chain identity** — only authorized signers (`authorizedSigners[address]`) can originate messages; the relayer is a separate `onlyRelayer` role.
4. **Replay protection** — 3 layers: `processedMessages[messageHash]`, `usedNonces[nonce]`, `processedTxHashes[sourceTxHash]`.
5. **Finality confirmation** — source block headers must be submitted (`submitBlockHeader`) and reach `requiredConfirmations` before the message can be processed.

See [docs/VERIFICATION.md](docs/VERIFICATION.md) for the test plan and on-chain verification commands.

## Documentation

- [Quickstart](docs/QUICKSTART.md) — installation, first bridge in 5 minutes
- [Architecture](docs/ARCHITECTURE.md) — protocol, message format, security model
- [API Reference](docs/API.md) — Python SDK + CLI
- [Verification](docs/VERIFICATION.md) — how to verify the 5 dimensions end-to-end
- [Examples](examples/) — runnable code samples

## Project Status

This is the **0.1.0** release. The protocol is functional and tested end-to-end with `py-evm` local nodes. Production deployment requires:

- A real Byzantine-fault-tolerant consensus layer (we use `py-evm` for development only)
- Production-grade Merkle tree implementation (current `verifySingleLeafProof` is a single-leaf simplification)
- Hardware key management for the relayer
- A more sophisticated source-chain event log indexer

## Development

```bash
git clone https://github.com/yourorg/xchain-sdk
cd xchain-sdk
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src tests
black --check src tests
mypy src

# Build
python3 -m build
```

## Contributing

Issues and PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) (TODO).

## License

MIT — see [LICENSE](LICENSE).
