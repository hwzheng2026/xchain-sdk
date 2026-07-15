"""xchain client - main entry point for the SDK.

Provides high-level API for interacting with the cross-chain middleware.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional, Dict, Any, List

from web3 import Web3
from web3.providers.rpc import HTTPProvider

from .contracts import load_artifact, get_contract
from .bridge_core import message_hash, encode_message, MessageType


class XChainClient:
    """High-level client for the cross-chain middleware.

    Two-chain model: source chain (where assets originate) and target chain
    (where mirrored assets are managed). Both must be EVM-compatible.

    Example:
        >>> bcos = ChainEndpoint("bcos", "http://127.0.0.1:8545", chain_id=1001)
        >>> cm = ChainEndpoint("chainmaker", "http://127.0.0.1:8546", chain_id=2001)
        >>> client = XChainClient(
        ...     source=bcos, target=cm,
        ...     source_contracts=deploy_bcos,
        ...     target_contracts=deploy_cm,
        ...     deployer="0x...",
        ... )
        >>> tx = client.mint_on_source(warehouse="GZJJ-2024-001", vintage=2024, valuation=500000, holder="0x...")
    """

    def __init__(
        self,
        source: "ChainEndpoint",
        target: "ChainEndpoint",
        source_contracts: Dict[str, str],
        target_contracts: Dict[str, str],
        deployer: str,
        artifacts_path: Optional[Path] = None,
    ):
        self.source = source
        self.target = target
        self.deployer = deployer
        self.source_contracts = source_contracts
        self.target_contracts = target_contracts
        self.artifacts_path = artifacts_path or Path(__file__).parent / "contracts" / "artifacts"

        # Load contract factories
        self._compiled = load_artifact("compiled.json", self.artifacts_path)

        # Source chain contracts
        self.source_cert = get_contract(
            source.w3, source_contracts["LiquorCertificate"],
            self._compiled["LiquorCertificate"]["abi"]
        )
        self.source_pledge = get_contract(
            source.w3, source_contracts["LiquorPledge"],
            self._compiled["LiquorPledge"]["abi"]
        )
        self.source_bridge = get_contract(
            source.w3, source_contracts["CrossChainBridge"],
            self._compiled["CrossChainBridge"]["abi"]
        )

        # Target chain contracts
        self.target_cert = get_contract(
            target.w3, target_contracts["LiquorCertificate"],
            self._compiled["LiquorCertificate"]["abi"]
        )
        self.target_pledge = get_contract(
            target.w3, target_contracts["LiquorPledge"],
            self._compiled["LiquorPledge"]["abi"]
        )
        self.target_bridge = get_contract(
            target.w3, target_contracts["CrossChainBridge"],
            self._compiled["CrossChainBridge"]["abi"]
        )

    # ============================================================
    # Source chain operations
    # ============================================================

    def mint_on_source(
        self,
        cert_id: str,
        warehouse_code: str,
        vintage: int,
        valuation: int,
        holder: str,
        cross_chain_hash: Optional[str] = None,
        origin_chain_id: Optional[int] = None,
        gas: int = 800000,
    ) -> Dict[str, Any]:
        """Mint a digital certificate on the source chain.

        Args:
            cert_id: 32-byte hex id of the certificate
            warehouse_code: human-readable warehouse identifier
            vintage: production year (1900-2100)
            valuation: certificate valuation (integer, business-defined unit)
            holder: recipient address
            cross_chain_hash: anchor hash for cross-chain (optional, auto-computed)
            origin_chain_id: source chain id (defaults to source.chain_id)
            gas: tx gas limit

        Returns:
            dict with keys: tx_hash, block_number, gas_used, status
        """
        if cross_chain_hash is None:
            cross_chain_hash = "0x" + Web3.keccak(
                bytes.fromhex(cert_id[2:]) + bytes.fromhex(holder[2:].lower())
            ).hex()
        if origin_chain_id is None:
            origin_chain_id = self.source.chain_id

        tx = self.source_cert.functions.mint(
            Web3.to_checksum_address(holder),
            cert_id, warehouse_code, vintage, valuation,
            cross_chain_hash, origin_chain_id,
        ).build_transaction({
            "from": self.deployer,
            "gas": gas,
            "gasPrice": Web3.to_wei(1, "gwei"),
        })
        return self.source.send_tx(tx)

    def get_source_certificate(self, cert_id: str) -> Dict[str, Any]:
        """Read a certificate from the source chain."""
        c = self.source_cert.functions.getCertificate(cert_id).call()
        return {
            "certId": c[0].hex() if isinstance(c[0], bytes) else c[0],
            "warehouseCode": c[1],
            "vintage": c[2],
            "holder": c[3],
            "valuation": c[4],
            "pledgeStatus": c[5],
            "crossChainHash": c[6].hex() if isinstance(c[6], bytes) else c[6],
            "originChainId": c[7],
            "mintedAt": c[8],
        }

    # ============================================================
    # Cross-chain operations
    # ============================================================

    def bridge_mint(
        self,
        cert_id: str,
        target_recipient: str,
        warehouse_code: str,
        vintage: int,
        valuation: int,
        gas: int = 800000,
    ) -> Dict[str, Any]:
        """Bridge a certificate from source to target chain.

        Performs 3 on-chain transactions on the target chain:
        1. submitBlockHeader — anchors source block header (eventRoot)
        2. processMessage — verifies and mints the mirror certificate
        3. (auto) records the cross-chain hash mapping

        Returns dict with tx hashes for each step.
        """
        # 1. Build the cross-chain message
        import time
        source_cert = self.get_source_certificate(cert_id)
        block = self.source.w3.eth.get_block(source_cert.get("_blockNumber")) if "_blockNumber" in source_cert else None

        # Need the source block that minted the cert - query from contract
        # Simpler: use latest finalized block
        source_block = self.source.w3.eth.block_number
        blk = self.source.w3.eth.get_block(source_block)
        block_hash = blk.hash.hex() if hasattr(blk.hash, "hex") else blk.hash
        if not block_hash.startswith("0x"):
            block_hash = "0x" + block_hash
        ts = blk.timestamp

        # Compute target cert id
        target_cert_id = "0x" + Web3.keccak(
            bytes.fromhex(cert_id[2:]) + self.target.chain_id.to_bytes(8, "big")
        ).hex()

        nonce = int(time.time() * 1000) % (10**12)

        msg = {
            "messageType": MessageType.MINT,
            "sourceChainId": self.source.chain_id,
            "sourceBlockNumber": source_block,
            "sourceTxHash": "0x" + "0" * 64,  # placeholder; real impl reads from event log
            "sourceOwner": "0x" + "0" * 40,
            "targetRecipient": target_recipient,
            "sourceCertId": cert_id,
            "targetCertId": target_cert_id,
            "warehouseCode": warehouse_code,
            "vintage": vintage,
            "valuation": valuation,
            "pledgeAmount": 0,
            "nonce": nonce,
            "merkleRoot": "0x" + "0" * 64,
            "leafIndex": 0,
        }
        mh = message_hash(msg)
        msg["messageHash"] = mh
        msg["merkleRoot"] = mh  # demo: eventRoot = messageHash (single-leaf tree)

        # 2. Submit source block header to target bridge
        tx_h = self.target_bridge.functions.submitBlockHeader(
            source_block, block_hash, mh, ts
        ).build_transaction({
            "from": self.deployer, "gas": 300000, "gasPrice": Web3.to_wei(1, "gwei"),
        })
        r_h = self.target.send_tx(tx_h)

        # 3. Process the cross-chain message
        merkle_proof = []
        msg_tuple = (
            mh, msg["messageType"], msg["sourceChainId"], msg["sourceBlockNumber"],
            msg["sourceTxHash"], msg["sourceOwner"], msg["targetRecipient"],
            msg["sourceCertId"], msg["targetCertId"], msg["warehouseCode"],
            msg["vintage"], msg["valuation"], msg["pledgeAmount"], msg["nonce"],
            msg["merkleRoot"], merkle_proof, msg["leafIndex"],
        )
        tx_m = self.target_bridge.functions.processMessage(msg_tuple).build_transaction({
            "from": self.deployer, "gas": gas, "gasPrice": Web3.to_wei(1, "gwei"),
        })
        r_m = self.target.send_tx(tx_m)

        return {
            "targetCertId": target_cert_id,
            "messageHash": mh,
            "submit_header": r_h,
            "process_message": r_m,
        }

    def get_target_certificate(self, target_cert_id: str) -> Dict[str, Any]:
        """Read a mirror certificate from the target chain."""
        c = self.target_cert.functions.getCertificate(target_cert_id).call()
        return {
            "certId": c[0].hex() if isinstance(c[0], bytes) else c[0],
            "warehouseCode": c[1],
            "vintage": c[2],
            "holder": c[3],
            "valuation": c[4],
            "pledgeStatus": c[5],
            "crossChainHash": c[6].hex() if isinstance(c[6], bytes) else c[6],
            "originChainId": c[7],
            "mintedAt": c[8],
        }

    def get_mapping_source_to_target(self, source_cert_id: str) -> str:
        """Get the target cert id for a given source cert id (from bridge)."""
        result = self.target_bridge.functions.sourceToTarget(source_cert_id).call()
        return result.hex() if isinstance(result, bytes) else result


class ChainEndpoint:
    """Represents one EVM chain endpoint."""

    def __init__(self, name: str, rpc_url: str, chain_id: int):
        self.name = name
        self.rpc_url = rpc_url
        self.chain_id = chain_id
        self.w3 = Web3(HTTPProvider(rpc_url))

    def __repr__(self):
        return f"ChainEndpoint(name={self.name!r}, chain_id={self.chain_id}, rpc={self.rpc_url})"

    def is_connected(self) -> bool:
        return self.w3.is_connected()

    def block_number(self) -> int:
        return self.w3.eth.block_number

    def send_tx(self, tx: Dict[str, Any]) -> Dict[str, Any]:
        """Send a transaction and wait for receipt.

        Args:
            tx: dict with at least 'from', 'to', 'data', 'gas', 'gasPrice'

        Returns:
            dict with tx_hash, block_number, status, gas_used
        """
        import time
        # sign + send via eth_sendTransaction (relies on unlocked account or geth personal mode)
        # For py-evm/eth-tester backend: eth_sendTransaction is supported
        w3 = self.w3
        nonce = w3.eth.get_transaction_count(tx["from"])
        full_tx = {
            "from": tx["from"],
            "to": tx.get("to"),
            "data": tx.get("data"),
            "value": tx.get("value", 0),
            "gas": tx["gas"],
            "gasPrice": tx.get("gasPrice", Web3.to_wei(1, "gwei")),
            "nonce": nonce,
        }
        txh = w3.eth.send_transaction(full_tx)
        # Wait for receipt
        for _ in range(120):
            r = w3.eth.get_transaction_receipt(txh)
            if r:
                return {
                    "tx_hash": txh.hex() if hasattr(txh, "hex") else txh,
                    "block_number": r.blockNumber,
                    "status": r.status,
                    "gas_used": r.gasUsed,
                }
            time.sleep(0.5)
        raise TimeoutError(f"No receipt for tx {txh}")
