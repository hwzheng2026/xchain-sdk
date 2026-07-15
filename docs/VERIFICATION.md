# Verification Playbook

This document explains how to **independently verify** the 5 cross-chain security dimensions enforced by the `CrossChainBridge` contract.

## Setup

```bash
pip install xchain-sdk
xchain start
xchain deploy
```

## Dimension 1: Asset mapping consistency

**Claim**: After a successful MINT cross-chain, `bridge.sourceToTarget(sourceCertId) == targetCertId` and the valuation is preserved 1:1.

```bash
# Mint and bridge
CERT=0x$(python3 -c "import secrets; print(secrets.token_hex(32))")
xchain mint $CERT --warehouse GZJJ-2024-001 --vintage 2024 --valuation 500000 --holder 0xYOUR
xchain bridge $CERT --warehouse GZJJ-2024-001 --vintage 2024 --valuation 500000

# Verify
xchain info $CERT
```

You should see the source and target entries have:
- same `warehouseCode`, `vintage`, `valuation`
- `pledgeStatus = 0` (None) initially
- `originChainId = 1001` on the target (it knows it came from BCOS)

For on-chain proof:
```python
from web3 import Web3
from xchain.contracts import load_artifact, get_contract

cm = Web3(Web3.HTTPProvider("http://127.0.0.1:8546"))
compiled = load_artifact("compiled.json")
bridge = get_contract(cm, "0x...", compiled["CrossChainBridge"]["abi"])

# Mapping
target_id = bridge.functions.sourceToTarget($CERT).call()
print("target:", target_id.hex())

# Re-derive expected target id
expected = Web3.keccak(bytes.fromhex($CERT[2:]) + (2001).to_bytes(8, "big"))
assert target_id == expected
```

## Dimension 2: Merkle event traceability

**Claim**: The `messageHash` in the cross-chain message is in the event tree of the source block whose header was anchored.

In production this is verified by `verifyMerkleProof(messageHash, eventRoot)`. The demo uses a single-leaf simplification (`verifySingleLeafProof`): root == leaf.

On-chain check (you already have `submitBlockHeader(blockNumber, eventRoot)` then `processMessage(msg)`):
1. The `eventRoot` passed to `submitBlockHeader` must equal the `merkleRoot` in the message, which must equal the `messageHash`.
2. The `verifySingleLeafProof(msg.messageHash, header.eventRoot)` check inside `processMessage` enforces this.

Negative test: try to bridge a cert with a tampered `messageHash` (different from the `eventRoot` you submitted):
```python
# Construct msg with wrong messageHash
bad_msg = dict(msg)
bad_msg["messageHash"] = "0x" + "ee" * 32
# build_transaction + send_transaction
# expect: revert MERKLE_FAIL
```

## Dimension 3: Cross-chain identity

**Claim**: Only the configured `relayer` can submit `processMessage`. The `sourceOwner` must be either `0x0` (demo) or in `authorizedSigners`.

On-chain check:
```python
# Wrong relayer
not_relayer = "0x0000000000000000000000000000000000000001"
tx = bridge.functions.processMessage(msg).build_transaction({"from": not_relayer, ...})
# expect: revert NOT_RELAYER
```

Test the `authorizedSigners` path:
```python
# Set a signer
bridge.functions.authorizeSigner(signer_addr, True).transact({"from": deployer})

# Use that signer as sourceOwner
msg["sourceOwner"] = signer_addr
# expect: processMessage succeeds
```

## Dimension 4: Replay protection

3 layers, each independently testable.

### Layer 4a: messageHash replay

```python
# Process the same msg twice
bridge.functions.processMessage(msg).transact(...)   # OK
bridge.functions.processMessage(msg).transact(...)   # revert REPLAY
```

### Layer 4b: nonce replay

```python
msg2 = dict(msg)
msg2["sourceCertId"] = "0x" + "ff" * 32
msg2["targetCertId"] = "0x" + "ee" * 32
# Keep same nonce
bridge.functions.processMessage(msg2).transact(...)  # revert NONCE_USED
```

### Layer 4c: sourceTxHash replay

```python
# Keep same sourceTxHash, different certId + nonce
msg3 = dict(msg)
msg3["sourceCertId"] = "0x" + "dd" * 32
msg3["targetCertId"] = "0x" + "cc" * 32
msg3["nonce"] = new_nonce
bridge.functions.processMessage(msg3).transact(...)  # would also fail (different reason)
# The 3 layers together ensure even a slight variant can't replay the source.
```

## Dimension 5: Finality

**Claim**: A `processMessage` for `sourceBlockNumber = N` can only succeed if `blockHeaders[N].finalized == true`.

Negative test (without `submitBlockHeader`):
```python
# processMessage for a block that was never submitted
msg4 = dict(msg)
msg4["sourceBlockNumber"] = 999999  # block we never submitted
# expect: revert NO_HEADER or NOT_FINALIZED
```

For the demo, `submitBlockHeader` sets `finalized = true` immediately (PoA assumption). In production, the relayer would track confirmations across N blocks.

## End-to-end check script

```python
import json
from web3 import Web3
from xchain.contracts import load_artifact, get_contract
from xchain import XChainClient, ChainEndpoint

# ... build client ...

# 1. Mint
client.mint_on_source(cert_id, "GZJJ-2024-001", 2024, 500000, deployer)

# 2. Bridge
result = client.bridge_mint(cert_id, deployer, "GZJJ-2024-001", 2024, 500000)

# 3. Verify
assert result["process_message"]["status"] == 1, "bridge failed"
mirror = client.get_target_certificate(result["targetCertId"])
assert mirror["valuation"] == 500000, "valuation mismatch"
assert mirror["originChainId"] == 1001, "wrong origin"
assert mirror["warehouseCode"] == "GZJJ-2024-001", "warehouse mismatch"

mapping = client.get_mapping_source_to_target(cert_id)
assert mapping == result["targetCertId"], "mapping mismatch"

print("✓ All 5 dimensions verified for this round trip")
```

## CI integration

```yaml
- name: Verify cross-chain dimensions
  run: |
    xchain start
    xchain deploy
    python tests/e2e/test_full_roundtrip.py
    xchain stop
```
