"""xchain CLI - command line interface for the cross-chain SDK.

Usage:
    xchain start                  Start both BCOS and ChainMaker dev chains
    xchain stop                   Stop both chains
    xchain status                 Show chain status
    xchain deploy                 Deploy all 3 contracts to both chains
    xchain mint <cert_id> ...     Mint a certificate on BCOS
    xchain bridge <cert_id> ...   Bridge a certificate BCOS -> ChainMaker
    xchain info <cert_id>         Show certificate info (searches both chains)
    xchain demo                   Run the full end-to-end demo
    xchain explorer               Interactive block/tx explorer
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from ..client import XChainClient, ChainEndpoint


def main(argv: Optional[list] = None):
    parser = argparse.ArgumentParser(
        prog="xchain",
        description="xchain-sdk: cross-chain middleware CLI (FISCO BCOS ↔ ChainMaker)",
    )
    sub = parser.add_subparsers(dest="cmd", help="sub-commands")

    # start
    p_start = sub.add_parser("start", help="Start both BCOS and ChainMaker dev chains")
    p_start.add_argument("--data-root", type=Path, default=Path("./xchain-data"))

    # stop
    sub.add_parser("stop", help="Stop both chains")

    # status
    sub.add_parser("status", help="Show chain status")

    # deploy
    sub.add_parser("deploy", help="Deploy all contracts to both chains")

    # mint
    p_mint = sub.add_parser("mint", help="Mint a certificate on BCOS")
    p_mint.add_argument("cert_id", help="Certificate ID (0x... 32 bytes)")
    p_mint.add_argument("--warehouse", required=True, help="Warehouse code")
    p_mint.add_argument("--vintage", type=int, required=True, help="Vintage year")
    p_mint.add_argument("--valuation", type=int, required=True, help="Valuation (integer)")
    p_mint.add_argument("--holder", required=True, help="Holder address")

    # bridge
    p_bridge = sub.add_parser("bridge", help="Bridge a certificate BCOS -> ChainMaker")
    p_bridge.add_argument("cert_id", help="Source certificate ID")
    p_bridge.add_argument("--warehouse", required=True)
    p_bridge.add_argument("--vintage", type=int, required=True)
    p_bridge.add_argument("--valuation", type=int, required=True)

    # info
    p_info = sub.add_parser("info", help="Show certificate info")
    p_info.add_argument("cert_id", help="Certificate ID")

    # demo
    sub.add_parser("demo", help="Run full end-to-end demo")

    # explorer
    sub.add_parser("explorer", help="Interactive block/tx explorer")

    args = parser.parse_args(argv)

    if not args.cmd:
        parser.print_help()
        sys.exit(0)

    # Dispatch
    dispatch = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "deploy": cmd_deploy,
        "mint": cmd_mint,
        "bridge": cmd_bridge,
        "info": cmd_info,
        "demo": cmd_demo,
        "explorer": cmd_explorer,
    }
    handler = dispatch[args.cmd]
    sys.exit(handler(args))


# ============================================================
# Handlers
# ============================================================

_CONFIG_PATH = Path.home() / ".xchain" / "config.json"


def _load_config() -> dict:
    """Load xchain runtime config (chain endpoints, deployer, contract addresses)."""
    if not _CONFIG_PATH.exists():
        print(f"ERROR: config not found at {_CONFIG_PATH}", file=sys.stderr)
        print("Run `xchain start` and `xchain deploy` first.", file=sys.stderr)
        sys.exit(1)
    return json.load(open(_CONFIG_PATH))


def _save_config(cfg: dict):
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def cmd_start(args):
    """Start both chains via ChainManager."""
    from .manager import ChainManager
    mgr = ChainManager(data_root=args.data_root)
    bcos = mgr.start("bcos", chain_id=1001, port=8545)
    cm = mgr.start("chainmaker", chain_id=2001, port=8546)

    # Save config for subsequent commands
    cfg = {
        "data_root": str(args.data_root),
        "bcos": {"chain_id": 1001, "rpc_url": bcos.rpc_url, "pid": bcos.pid},
        "chainmaker": {"chain_id": 2001, "rpc_url": cm.rpc_url, "pid": cm.pid},
        "deployer": bcos.w3.eth.accounts[0] if hasattr(bcos, "w3") else "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf",
    }
    _save_config(cfg)
    print("✓ Started both chains")
    print(mgr.status())
    return 0


def cmd_stop(args):
    """Stop both chains by reading config + killing PIDs."""
    cfg = _load_config()
    import os, signal
    for name in ("bcos", "chainmaker"):
        pid = cfg[name].get("pid")
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"✓ Stopped {name} (pid {pid})")
            except ProcessLookupError:
                print(f"  {name} (pid {pid}) already stopped")
    if _CONFIG_PATH.exists():
        _CONFIG_PATH.unlink()
    return 0


def cmd_status(args):
    """Show both chain statuses."""
    cfg = _load_config()
    from web3 import Web3
    from web3.providers.rpc import HTTPProvider

    print(f"xchain status @ {_CONFIG_PATH}")
    print(f"  data root: {cfg.get('data_root', 'n/a')}")
    print()
    for name in ("bcos", "chainmaker"):
        chain_cfg = cfg[name]
        w3 = Web3(HTTPProvider(chain_cfg["rpc_url"]))
        connected = w3.is_connected()
        block = w3.eth.block_number if connected else "n/a"
        print(f"  {name:14s} chain_id={chain_cfg['chain_id']:4d}  block={block}  {'✓' if connected else '✗'}  pid={chain_cfg.get('pid', 'n/a')}")
    return 0


def cmd_deploy(args):
    """Deploy all 3 contracts to both chains."""
    cfg = _load_config()
    from ..chain.nodes import EVMRPCServer
    # Actually use a deploy script
    import subprocess
    deploy_script = Path(__file__).parent.parent.parent.parent / "scripts" / "deploy_contracts.py"
    # Fall back to inline deploy
    from web3 import Web3
    from web3.providers.rpc import HTTPProvider
    from ..contracts import load_artifact, get_contract

    compiled = load_artifact("compiled.json")
    deployer = cfg.get("deployer", "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf")

    deployments = {}
    for name in ("bcos", "chainmaker"):
        chain_cfg = cfg[name]
        w3 = Web3(HTTPProvider(chain_cfg["rpc_url"]))
        deployed = {"LiquorCertificate": None, "LiquorPledge": None, "CrossChainBridge": None}
        # Deploy in order: Cert -> Pledge -> Bridge (Bridge depends on Cert+Pledge)
        for contract_name in ["LiquorCertificate", "LiquorPledge", "CrossChainBridge"]:
            ctor = compiled[contract_name]["abi"]
            bytecode = compiled[contract_name]["bin" if "bin" in compiled[contract_name] else "bytecode"]
            Contract = w3.eth.contract(abi=ctor, bytecode=bytecode)
            tx = Contract.constructor().build_transaction({
                "from": deployer, "gas": 5000000, "gasPrice": Web3.to_wei(1, "gwei"),
            })
            txh = w3.eth.send_transaction(tx)
            r = w3.eth.wait_for_transaction_receipt(txh, timeout=60)
            if r.status != 1:
                print(f"✗ {name} {contract_name} deploy failed")
                return 1
            deployed[contract_name] = r.contractAddress
            print(f"  ✓ {name} {contract_name} @ {r.contractAddress}")
        deployments[name] = {
            "chainId": chain_cfg["chain_id"],
            "chainName": name,
            "rpcUrl": chain_cfg["rpc_url"],
            "deployer": deployer,
            "contracts": deployed,
        }

    # Save deployments
    cfg["deployments"] = deployments
    _save_config(cfg)
    deploy_file = Path(cfg.get("data_root", "./xchain-data")) / "deployments.json"
    deploy_file.parent.mkdir(parents=True, exist_ok=True)
    with open(deploy_file, "w") as f:
        json.dump(deployments, f, indent=2)
    print(f"\n✓ Deployments saved to {deploy_file}")
    return 0


def cmd_mint(args):
    """Mint a certificate via the SDK."""
    cfg = _load_config()
    if "deployments" not in cfg:
        print("ERROR: contracts not deployed. Run `xchain deploy` first.", file=sys.stderr)
        return 1
    client = _make_client(cfg)
    result = client.mint_on_source(
        cert_id=args.cert_id,
        warehouse_code=args.warehouse,
        vintage=args.vintage,
        valuation=args.valuation,
        holder=args.holder,
    )
    if result["status"] == 1:
        print(f"✓ Minted cert {args.cert_id[:18]}... (block {result['block_number']}, gas {result['gas_used']})")
    else:
        print(f"✗ Mint failed: {result}")
        return 1
    return 0


def cmd_bridge(args):
    """Bridge a certificate."""
    cfg = _load_config()
    if "deployments" not in cfg:
        print("ERROR: contracts not deployed. Run `xchain deploy` first.", file=sys.stderr)
        return 1
    client = _make_client(cfg)
    result = client.bridge_mint(
        cert_id=args.cert_id,
        target_recipient=cfg["deployer"],
        warehouse_code=args.warehouse,
        vintage=args.vintage,
        valuation=args.valuation,
    )
    pm = result["process_message"]
    if pm["status"] == 1:
        print(f"✓ Bridged cert to target (targetCertId={result['targetCertId'][:18]}...)")
    else:
        print(f"✗ Bridge failed: {pm}")
        return 1
    return 0


def cmd_info(args):
    """Show info for a certificate (searches both chains)."""
    cfg = _load_config()
    if "deployments" not in cfg:
        print("ERROR: contracts not deployed. Run `xchain deploy` first.", file=sys.stderr)
        return 1
    client = _make_client(cfg)
    try:
        src = client.get_source_certificate(args.cert_id)
        print(f"Source chain ({client.source.name}):")
        for k, v in src.items():
            print(f"  {k}: {v}")
    except Exception as e:
        print(f"  not on source: {e}")

    # Try to find the mirror on target
    try:
        target_id = client.get_mapping_source_to_target(args.cert_id)
        if target_id and target_id != "0x" + "0" * 64:
            tgt = client.get_target_certificate(target_id)
            print(f"\nTarget chain ({client.target.name}) — mirror {target_id[:18]}...:")
            for k, v in tgt.items():
                print(f"  {k}: {v}")
    except Exception as e:
        print(f"  not on target: {e}")
    return 0


def cmd_demo(args):
    """Run the full end-to-end demo."""
    print("Running xchain end-to-end demo...")
    print("(For full demo with pledge/unpledge, see scripts/demo_end_to_end.py)")
    cfg = _load_config()
    if "deployments" not in cfg:
        print("ERROR: contracts not deployed. Run `xchain deploy` first.", file=sys.stderr)
        return 1
    client = _make_client(cfg)
    import secrets
    cert_id = "0x" + secrets.token_hex(32)
    warehouse = "GZJJ-DEMO-001"
    vintage = 2024
    valuation = 500_000
    print(f"\n[1/3] Minting cert {cert_id[:18]}...")
    r = client.mint_on_source(cert_id, warehouse, vintage, valuation, cfg["deployer"])
    if r["status"] != 1:
        print(f"  ✗ mint failed")
        return 1
    print(f"  ✓ block {r['block_number']}")

    print(f"\n[2/3] Bridging to target chain...")
    r = client.bridge_mint(cert_id, cfg["deployer"], warehouse, vintage, valuation)
    if r["process_message"]["status"] != 1:
        print(f"  ✗ bridge failed")
        return 1
    print(f"  ✓ targetCertId={r['targetCertId'][:18]}...")

    print(f"\n[3/3] Reading mirror...")
    mirror = client.get_target_certificate(r["targetCertId"])
    print(f"  ✓ mirror on target:")
    for k, v in mirror.items():
        print(f"      {k}: {v}")
    print()
    print("Demo complete. Use `xchain info <cert_id>` for details.")
    return 0


def cmd_explorer(args):
    """Interactive block/tx explorer."""
    print("xchain explorer (type 'help' for commands, 'quit' to exit)")
    cfg = _load_config()
    from web3 import Web3
    from web3.providers.rpc import HTTPProvider
    bcos_w3 = Web3(HTTPProvider(cfg["bcos"]["rpc_url"]))
    cm_w3 = Web3(HTTPProvider(cfg["chainmaker"]["rpc_url"]))
    chains = {"bcos": bcos_w3, "chainmaker": cm_w3}

    while True:
        try:
            line = input("xchain> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line or line == "quit" or line == "exit":
            break
        if line == "help":
            print("  status              - show both chain statuses")
            print("  block <chain> [N]   - show latest N blocks (default 5)")
            print("  cert <chain> <id>   - show certificate by id")
            print("  bridge <chain>      - show bridge contract state")
            print("  quit                - exit")
            continue
        parts = line.split()
        try:
            if parts[0] == "status":
                for name, w3 in chains.items():
                    print(f"  {name}: block {w3.eth.block_number}")
            elif parts[0] == "block" and len(parts) >= 2:
                w3 = chains[parts[1]]
                n = int(parts[2]) if len(parts) > 2 else 5
                cur = w3.eth.block_number
                for b in range(max(0, cur - n + 1), cur + 1):
                    blk = w3.eth.get_block(b)
                    print(f"  #{b}  txs={len(blk.transactions)}  hash={blk.hash.hex()[:18]}...")
            elif parts[0] == "cert" and len(parts) >= 3:
                w3 = chains[parts[1]]
                from ..contracts import load_artifact
                compiled = load_artifact("compiled.json")
                cert_addr = cfg["deployments"][parts[1]]["contracts"]["LiquorCertificate"]
                c = w3.eth.contract(address=cert_addr, abi=compiled["LiquorCertificate"]["abi"])
                cd = c.functions.getCertificate(parts[2]).call()
                print(f"  cert {parts[2][:18]}...")
                print(f"    holder={cd[3]}")
                print(f"    warehouse={cd[1]}")
                print(f"    vintage={cd[2]}")
                print(f"    valuation={cd[4]:,}")
                print(f"    pledgeStatus={cd[5]}")
                print(f"    originChainId={cd[7]}")
            elif parts[0] == "bridge" and len(parts) >= 2:
                w3 = chains[parts[1]]
                from ..contracts import load_artifact
                compiled = load_artifact("compiled.json")
                bridge_addr = cfg["deployments"][parts[1]]["contracts"]["CrossChainBridge"]
                b = w3.eth.contract(address=bridge_addr, abi=compiled["CrossChainBridge"]["abi"])
                print(f"  owner       = {b.functions.owner().call()}")
                print(f"  relayer     = {b.functions.relayer().call()}")
                print(f"  peerChainId = {b.functions.peerChainId().call()}")
            else:
                print("  unknown command. type 'help'.")
        except Exception as e:
            print(f"  error: {e}")
    return 0


def _make_client(cfg: dict) -> XChainClient:
    """Build an XChainClient from saved config."""
    bcos_ep = ChainEndpoint("bcos", cfg["bcos"]["rpc_url"], cfg["bcos"]["chain_id"])
    cm_ep = ChainEndpoint("chainmaker", cfg["chainmaker"]["rpc_url"], cfg["chainmaker"]["chain_id"])
    return XChainClient(
        source=bcos_ep, target=cm_ep,
        source_contracts=cfg["deployments"]["bcos"]["contracts"],
        target_contracts=cfg["deployments"]["chainmaker"]["contracts"],
        deployer=cfg["deployer"],
    )


if __name__ == "__main__":
    main()
