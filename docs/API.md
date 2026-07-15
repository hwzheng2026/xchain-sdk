# API Reference

## Python SDK

### `xchain.XChainClient`

High-level cross-chain client. Holds references to both chains and all 3 contracts on each.

```python
from xchain import XChainClient, ChainEndpoint

bcos = ChainEndpoint("bcos", "http://127.0.0.1:8545", chain_id=1001)
cm = ChainEndpoint("chainmaker", "http://127.0.0.1:8546", chain_id=2001)

client = XChainClient(
    source=bcos,
    target=cm,
    source_contracts={"LiquorCertificate": "0x...", "LiquorPledge": "0x...", "CrossChainBridge": "0x..."},
    target_contracts={"LiquorCertificate": "0x...", "LiquorPledge": "0x...", "CrossChainBridge": "0x..."},
    deployer="0x...",
)
```

#### `client.mint_on_source(cert_id, warehouse_code, vintage, valuation, holder, cross_chain_hash=None, origin_chain_id=None, gas=800000) -> dict`

Mint a certificate on the source chain. Returns `{tx_hash, block_number, status, gas_used}`.

- `cert_id`: `0x`-prefixed 32-byte hex
- `warehouse_code`: human-readable warehouse id (e.g. `"GZJJ-2024-001"`)
- `vintage`: int year (1900-2100)
- `valuation`: int valuation in business-defined unit (integer yuan for RMB)
- `holder`: `0x`-prefixed address
- `cross_chain_hash`: optional; computed as `keccak(certId || holder)` if not provided
- `origin_chain_id`: optional; defaults to `source.chain_id`

#### `client.get_source_certificate(cert_id) -> dict`

Returns a dict with keys: `certId`, `warehouseCode`, `vintage`, `holder`, `valuation`, `pledgeStatus`, `crossChainHash`, `originChainId`, `mintedAt`.

#### `client.bridge_mint(cert_id, target_recipient, warehouse_code, vintage, valuation, gas=800000) -> dict`

Performs the full MINT cross-chain flow on the target chain. Returns:

```python
{
    "targetCertId": "0x...",  # the mirror cert id
    "messageHash": "0x...",
    "submit_header": {"tx_hash": ..., "block_number": ..., "status": ..., "gas_used": ...},
    "process_message": {"tx_hash": ..., "block_number": ..., "status": ..., "gas_used": ...},
}
```

#### `client.get_target_certificate(target_cert_id) -> dict`

Read a mirror certificate from the target chain. Same keys as `get_source_certificate`.

#### `client.get_mapping_source_to_target(source_cert_id) -> str`

Query the bridge's `sourceToTarget` mapping. Returns the target cert id (or zero bytes if not mapped).

### `xchain.ChainEndpoint`

```python
from xchain import ChainEndpoint
bcos = ChainEndpoint("bcos", "http://127.0.0.1:8545", chain_id=1001)
bcos.is_connected()        # bool
bcos.block_number()        # int
bcos.w3                    # underlying Web3 instance
```

### `xchain.chain.ChainManager`

```python
from xchain.chain import ChainManager
mgr = ChainManager(data_root=Path("./xchain-data"))
bcos = mgr.start(name="bcos", chain_id=1001, port=8545)
cm = mgr.start(name="chainmaker", chain_id=2001, port=8546)
mgr.status()                # multi-line status string
mgr.stop_all()
```

### `xchain.bridge_core`

```python
from xchain.bridge_core import (
    message_hash, encode_message, MessageType,
    MerkleTree, build_event_merkle_tree,
    ReplayProtector, FinalityTracker,
    verify_single_leaf_merkle,
)
```

- `message_hash(msg) -> str` — keccak256 of the ABI-encoded message; the on-chain `messageHash` field
- `MessageType` — enum-like: `MINT=0, BURN=1, PLEDGE=2, UNPLEDGE=3`
- `MerkleTree(leaves)` — full Merkle tree with proof generation and verification
- `ReplayProtector` — off-chain replay tracker (in addition to on-chain `processedMessages`)
- `FinalityTracker(minimum_confirmations=1)` — tracks block confirmations off-chain

### `xchain.relayer`

```python
from xchain.relayer import UnifiedRelayer
relayer = UnifiedRelayer(client=client, poll_interval=2.0)
relayer.start()  # blocks; polls both chains, calls processMessage on target
relayer.stop()
```

## CLI

### `xchain start [--data-root PATH]`

Start both dev chains. Writes `~/.xchain/config.json`.

### `xchain stop`

Stop both dev chains. Removes `~/.xchain/config.json`.

### `xchain status`

Print block heights, connection status, PIDs for both chains.

### `xchain deploy`

Deploy the 3 contracts to both chains. Updates `~/.xchain/config.json` and `<data-root>/deployments.json`.

### `xchain mint <cert_id> --warehouse W --vintage Y --valuation V --holder H`

Mint on BCOS.

### `xchain bridge <cert_id> --warehouse W --vintage Y --valuation V`

Bridge to ChainMaker.

### `xchain info <cert_id>`

Show the cert on BCOS + (if bridged) the mirror on ChainMaker.

### `xchain demo`

Mint + bridge a random cert. Quick sanity check.

### `xchain explorer`

Interactive REPL. Commands:
- `status`
- `block <chain> [N]`
- `cert <chain> <cert_id>`
- `bridge <chain>`
- `quit`
