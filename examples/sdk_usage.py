"""SDK usage example — how to embed xchain-sdk in your own application.

This example shows the SDK API surface: connect, mint, bridge, read.
"""
from __future__ import annotations
import secrets

from xchain import XChainClient, ChainEndpoint
from xchain.contracts import load_artifact, get_contract


def setup_client(rpc_bcos="http://127.0.0.1:8545", rpc_cm="http://127.0.0.1:8546") -> XChainClient:
    """Build a client connected to the local dev chains.

    For production: change RPC URLs and contract addresses.
    """
    bcos = ChainEndpoint("bcos", rpc_bcos, chain_id=1001)
    cm = ChainEndpoint("chainmaker", rpc_cm, chain_id=2001)

    # In production, these addresses come from your deployment registry
    # (e.g. a config file, env vars, or a service like a contract registry)
    bcos_contracts = {
        "LiquorCertificate": "0x6D411e0A54382eD43F02410Ce1c7a7c122afA6E1",
        "LiquorPledge": "0x5CF7F96627F3C9903763d128A1cc5D97556A6b99",
        "CrossChainBridge": "0xA3183498b579bd228aa2B62101C40CC1da978F24",
    }
    cm_contracts = bcos_contracts  # same addresses on both in the dev env

    return XChainClient(
        source=bcos, target=cm,
        source_contracts=bcos_contracts,
        target_contracts=cm_contracts,
        deployer="0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf",  # your relayer EOA
    )


def issue_and_bridge_demo():
    """Issue a digital liquor certificate and bridge it to the other chain."""
    client = setup_client()

    # Generate a unique cert id (in production: derive from business data)
    cert_id = "0x" + secrets.token_hex(32)
    warehouse = "GZJJ-2024-001"
    vintage = 2024
    valuation = 500_000  # 50万元 RMB
    holder = "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf"

    # 1. Mint on source
    print(f"Minting cert {cert_id[:18]}...")
    mint_result = client.mint_on_source(
        cert_id=cert_id,
        warehouse_code=warehouse,
        vintage=vintage,
        valuation=valuation,
        holder=holder,
    )
    print(f"  block={mint_result['block_number']} gas={mint_result['gas_used']}")

    # 2. Bridge to target
    print(f"Bridging to target chain...")
    bridge_result = client.bridge_mint(
        cert_id=cert_id,
        target_recipient=holder,
        warehouse_code=warehouse,
        vintage=vintage,
        valuation=valuation,
    )
    target_cert_id = bridge_result["targetCertId"]
    print(f"  mirror: {target_cert_id[:18]}...")

    # 3. Read back
    mirror = client.get_target_certificate(target_cert_id)
    print(f"\nMirror cert on target chain:")
    for k, v in mirror.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    issue_and_bridge_demo()
