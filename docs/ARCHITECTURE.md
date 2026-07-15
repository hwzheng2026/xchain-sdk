# Architecture

This document describes the cross-chain protocol, message format, and security model used by `xchain-sdk`.

## Overview

`xchain-sdk` is middleware for moving assets between two EVM-compatible consortium chains. The reference implementation is built around the **数字酒证 (digital liquor certificate)** use case — the asset is an NFT-like certificate representing a bonded warehouse receipt for a barrel of Guizhou sauce-aroma baijiu (贵州酱酒). The same code generalizes to any "lock-and-mint" cross-chain asset.

## Two-chain model

```
   ┌─────────────────┐                  ┌─────────────────┐
   │   Source chain  │                  │   Target chain  │
   │   (BCOS, 1001)  │                  │  (CM,    2001)  │
   │                 │                  │                 │
   │ LiquorCert      │                  │ LiquorCert      │
   │  - sourceOf     │                  │  - mirrorOf     │
   │ LiquorPledge    │                  │ LiquorPledge    │
   │ CrossChainBridge│◄──── relayer ───►│ CrossChainBridge│
   │  - peer = 2001  │   (off-chain)    │  - peer = 1001  │
   │  - relayer      │                  │  - relayer      │
   └─────────────────┘                  └─────────────────┘
```

- **Source chain**: the chain where the original asset lives.
- **Target chain**: the chain where the mirrored asset is created/locked/burned.
- **Relayer**: an off-chain agent that watches both chains, fetches source events, and submits the corresponding `processMessage` calls on the target chain.
- **Bridge contracts**: deployed on both chains; each is the `owner` and the `relayer` in the reference demo.

## Contract: `LiquorCertificate`

NFT-like contract holding a single asset type.

```solidity
struct Certificate {
    bytes32 certId;            // keccak256 of business salt
    string  warehouseCode;     // "GZJJ-2024-001"
    uint16  vintage;           // 2024
    address holder;            // current owner
    uint256 valuation;         // valuation in business unit (e.g. RMB integer-yuan)
    PledgeStatus pledgeStatus; // None/Pledged/Unlocked
    bytes32 crossChainHash;    // anchor hash
    uint256 originChainId;     // 1001 or 2001
    uint64  mintedAt;          // block.timestamp
}
```

Key functions:
- `mint(to, certId, warehouseCode, vintage, valuation, crossChainHash, originChainId)` — only `owner`
- `mintMirror(to, certId, warehouseCode, vintage, valuation, crossChainHash, originChainId, originSerial)` — only `bridgeContract`
- `burn(certId)` — only `bridgeContract`
- `getCertificate(certId)` — view

## Contract: `LiquorPledge`

Handles local + cross-chain pledges with a time lock.

- `pledge(certId, pledgee, lockDuration)` payable — locks ETH, sets status to `Pledged`
- `unpledge(certId)` — only after `lockDuration` elapses; sets status to `Unlocked`
- `completeCrossChainPledge(certId, ...)` payable — called by `bridgeContract` to record a cross-chain pledge

## Contract: `CrossChainBridge`

The heart of the cross-chain protocol. Stores source block headers, processes messages, and maintains the source-to-target mapping.

### State

```solidity
struct BlockHeader {
    bytes32 blockHash;
    bytes32 eventRoot;     // simplified: single-leaf Merkle root
    uint64  blockNumber;
    uint64  timestamp;
    bool    finalized;
}
mapping(uint256 => BlockHeader) public blockHeaders;
mapping(bytes32 => bytes32)     public sourceToTarget;
mapping(bytes32 => bytes32)     public targetToSource;
mapping(bytes32 => bool)        public processedMessages;
mapping(uint256 => bool)        public usedNonces;
mapping(bytes32 => uint256)     public messageNonces;
mapping(bytes32 => bool)        public processedTxHashes;
mapping(address => bool)        public authorizedSigners;
```

### Cross-chain message format

```solidity
struct CrossChainMessage {
    bytes32 messageHash;
    uint256 messageType;        // 0=MINT, 1=BURN, 2=PLEDGE, 3=UNPLEDGE
    uint256 sourceChainId;
    uint256 sourceBlockNumber;
    bytes32 sourceTxHash;
    address sourceOwner;
    address targetRecipient;
    bytes32 sourceCertId;
    bytes32 targetCertId;
    string  warehouseCode;
    uint16  vintage;
    uint256 valuation;
    uint256 pledgeAmount;
    uint256 nonce;
    bytes32 merkleRoot;
    bytes32[] merkleProof;
    uint256 leafIndex;
}
```

### Protocol flow (MINT example)

```
[Source]  user calls LiquorCertificate.mint(certId, ...)
         ↓ emits CertificateMinted(certId, ...)
[Relayer] watches event
         ↓ constructs CrossChainMessage
         ↓ submitBlockHeader(blockNumber, blockHash, eventRoot, timestamp)   on target
         ↓ processMessage(msg)                                              on target
[Target]  CrossChainBridge:
           1. !processedMessages[msg.messageHash]                            // 重放保护
           2. msg.sourceChainId == peerChainId                               // 链 ID
           3. !usedNonces[msg.nonce]
           4. blockHeaders[msg.sourceBlockNumber].finalized == true         // 最终性
           5. blockHeaders[msg.sourceBlockNumber].blockHash != 0
           6. verifySingleLeafProof(msg.messageHash, header.eventRoot)      // Merkle
           7. authorizedSigners[msg.sourceOwner] || msg.sourceOwner == 0    // 身份
           8. LiquorCertificate.mintMirror(...)                              // mint
           9. sourceToTarget[sourceCertId] = targetCertId
         ↓ emits CrossChainMint(...)
```

## The 5 verification dimensions

Every `processMessage` call enforces 5 layers of checks:

| # | Dimension | Mechanism |
|---|-----------|-----------|
| 1 | **Asset mapping consistency** | `sourceToTarget[sourceCertId]` must be unset; mapping set in same tx |
| 2 | **Merkle event traceability** | `verifySingleLeafProof(msgHash, header.eventRoot)` |
| 3 | **Cross-chain identity** | `onlyRelayer` + `authorizedSigners[sourceOwner]` |
| 4 | **Replay protection** | `processedMessages[msgHash]`, `usedNonces[nonce]`, `processedTxHashes[txHash]` |
| 5 | **Finality** | `header.finalized == true`; `requiredConfirmations` is incremented by relayer |

See [VERIFICATION.md](VERIFICATION.md) for the verification playbook.

## Production vs demo simplifications

| Concern | Demo | Production |
|---------|------|------------|
| Merkle proof | single leaf (= message hash) | full Merkle path with sibling hashes |
| Finality | 1 confirmation, instant | wait N blocks; track reorganization depth |
| Relayer keys | same EOA as deployer | HSM/KMS, separate signer for each chain |
| Event log indexer | manual in scripts | a robust indexer like The Graph |
| Persistence | in-memory (py-evm) | LevelDB / RocksDB; state survives restart |
| Chain backend | py-evm (testing) | geth / FISCO BCOS node / ChainMaker node |

## What `xchain-sdk` provides

- **`XChainClient`** — high-level Python API (mint, bridge, query)
- **`ChainEndpoint`** — represents one chain (Web3 + chain id + RPC URL)
- **`ChainManager`** — spawn/stop multiple local chains for development
- **`relayer.py`** — automated message relayer (polls source events → calls target)
- **`bridge_core.py`** — message hash, encoding, Merkle helpers, replay/finality trackers
- **CLI** — `xchain start/stop/status/deploy/mint/bridge/info/demo/explorer`

## What you bring

- A real consensus layer (FISCO BCOS, ChainMaker, Ethereum L2, etc.)
- Key management for the relayer
- A production Merkle tree implementation
- A monitoring layer (Prometheus, etc.)
- A frontend or backend that calls into `XChainClient` for business logic
