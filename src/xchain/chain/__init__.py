"""Chain node management - EVM-compatible chain node lifecycle.

Wraps the EVMRPCServer from nodes.py and exposes high-level helpers.
"""
from .nodes import EVMRPCServer
from .manager import ChainManager, ManagedChain


def start_node(name: str, chain_id: int, port: int, data_dir: str = "./data") -> EVMRPCServer:
    """Convenience wrapper: start a single chain node in the current process.

    For multi-chain development, prefer ChainManager.
    """
    import os
    os.makedirs(data_dir, exist_ok=True)
    server = EVMRPCServer(chain_id=chain_id, name=name, port=port, data_dir=data_dir)
    server.serve_forever()
    return server


def stop_node(server: EVMRPCServer):
    """Stop a running node server."""
    if server:
        server.shutdown()


def node_status(name: str, port: int) -> dict:
    """Check status of a node by name + port.

    Returns:
        dict with keys: name, port, connected, block_number
    """
    from web3 import Web3
    from web3.providers.rpc import HTTPProvider
    w3 = Web3(HTTPProvider(f"http://127.0.0.1:{port}"))
    return {
        "name": name,
        "port": port,
        "connected": w3.is_connected(),
        "block_number": w3.eth.block_number if w3.is_connected() else None,
    }


__all__ = [
    "EVMRPCServer",
    "ChainManager",
    "ManagedChain",
    "start_node",
    "stop_node",
    "node_status",
]
