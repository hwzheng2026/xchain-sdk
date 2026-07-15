# Quickstart

Get your first cross-chain bridge transaction in 5 minutes.

## Prerequisites

- Python 3.8 or newer
- `pip`

## 1. Install

```bash
pip install xchain-sdk
```

For the demo, no Docker / Java / external services needed — `xchain-sdk` uses `py-evm` (in-process EVM) to spin up two local chains.

## 2. Start the dev chains

```bash
xchain start
```

This starts:
- **BCOS** at `http://127.0.0.1:8545` (chain id 1001)
- **ChainMaker** at `http://127.0.0.1:8546` (chain id 2001)

Both are 10-account, 100-ETH-each dev chains, managed by `py-evm` in subprocess. State is in-memory (resets on stop/restart).

## 3. Deploy contracts

```bash
xchain deploy
```

This deploys 3 contracts to each chain:
- `LiquorCertificate` (NFT-like, holds the certificate data)
- `LiquorPledge` (pledge/unpledge logic)
- `CrossChainBridge` (the bridge; owner = relayer = first account)

## 4. Run the demo

```bash
xchain demo
```

This mints a cert on BCOS, bridges it to ChainMaker, and prints the mirror cert.

For a full end-to-end demo (with pledge, unpledge, business closure), see `examples/full_demo.py`.

## 5. Explore

```bash
xchain status
xchain explorer
```

In the explorer, try:
```
xchain> block bcos 5
xchain> cert bcos 0x...
xchain> bridge chainmaker
```

## 6. Stop

```bash
xchain stop
```

## What's next?

- See [ARCHITECTURE.md](ARCHITECTURE.md) for the protocol design
- See [API.md](API.md) for the full Python SDK reference
- See [VERIFICATION.md](VERIFICATION.md) for how to verify the 5 cross-chain dimensions
- See [examples/](examples/) for programmatic usage

## Troubleshooting

### "Address is not checksummed"

`web3.py` v7+ requires checksummed addresses. Use `Web3.to_checksum_address(addr)`.

### "RPC eth_call error: MERKLE_FAIL" / "ALREADY_MAPPED"

The demo is not idempotent by default — it generates a fresh `cert_id` on each run. If you supply your own, the bridge will reject duplicates.

### "Connection refused on :8545"

The chain isn't running. Run `xchain start` and wait for the "Started both chains" line before continuing.

### "Insufficient funds"

Each dev chain account starts with 100 ETH. If you burn through it, restart the chain (state is in-memory).
