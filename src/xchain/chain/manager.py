"""Multi-chain manager for running multiple EVM chains side-by-side.

Provides a unified API to start, stop, and query status of multiple chains
(e.g., FISCO BCOS simulator + ChainMaker simulator).
"""
from __future__ import annotations
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ManagedChain:
    """A running EVM chain node managed by ChainManager."""
    name: str
    chain_id: int
    rpc_url: str
    port: int
    pid: int
    data_dir: Path
    log_file: Path
    process: subprocess.Popen = field(repr=False)

    def is_alive(self) -> bool:
        return self.process.poll() is None

    def stop(self, timeout: int = 5):
        if self.is_alive():
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def status_line(self) -> str:
        alive = "✓ running" if self.is_alive() else "✗ stopped"
        return f"  {self.name:14s} (chain {self.chain_id}, :{self.port}, pid {self.pid})  {alive}"


class ChainManager:
    """Manages a fleet of EVM chain nodes for cross-chain development.

    Example:
        >>> mgr = ChainManager(data_root=Path("./my-chains"))
        >>> bcos = mgr.start(name="bcos", chain_id=1001, port=8545)
        >>> cm = mgr.start(name="chainmaker", chain_id=2001, port=8546)
        >>> mgr.status()
        >>> mgr.stop_all()
    """

    def __init__(self, data_root: Optional[Path] = None):
        self.data_root = Path(data_root) if data_root else Path("./xchain-data")
        self.data_root.mkdir(parents=True, exist_ok=True)
        self._chains: Dict[str, ManagedChain] = {}

    def start(
        self,
        name: str,
        chain_id: int,
        port: int,
        mnemonic: Optional[str] = None,
        accounts: int = 10,
    ) -> ManagedChain:
        """Start a new chain node (or no-op if already running with same config)."""
        if name in self._chains and self._chains[name].is_alive():
            return self._chains[name]

        chain_dir = self.data_root / name
        chain_dir.mkdir(parents=True, exist_ok=True)
        data_dir = chain_dir / "data"
        data_dir.mkdir(exist_ok=True)
        log_file = chain_dir / f"{name}.log"

        # Build command
        cmd = [
            "python3", "-m", "xchain.chain.nodes",
            "--chain-id", str(chain_id),
            "--name", name,
            "--port", str(port),
            "--data-dir", str(data_dir),
        ]
        if mnemonic:
            cmd.extend(["--mnemonic", mnemonic])
        if accounts:
            cmd.extend(["--accounts", str(accounts)])

        log_handle = open(log_file, "ab")
        proc = subprocess.Popen(
            cmd, stdout=log_handle, stderr=subprocess.STDOUT,
            cwd=str(self.data_root),
        )

        # Wait for RPC to come up
        from web3 import Web3
        from web3.providers.rpc import HTTPProvider
        w3 = Web3(HTTPProvider(f"http://127.0.0.1:{port}"))
        for _ in range(60):
            if w3.is_connected():
                break
            time.sleep(0.5)
        else:
            proc.kill()
            raise RuntimeError(f"Chain {name} did not start within 30s")

        managed = ManagedChain(
            name=name, chain_id=chain_id,
            rpc_url=f"http://127.0.0.1:{port}",
            port=port, pid=proc.pid,
            data_dir=data_dir, log_file=log_file,
            process=proc,
        )
        self._chains[name] = managed
        return managed

    def stop(self, name: str):
        if name in self._chains:
            self._chains[name].stop()
            del self._chains[name]

    def stop_all(self):
        for name in list(self._chains.keys()):
            self.stop(name)

    def get(self, name: str) -> ManagedChain:
        return self._chains[name]

    def list(self) -> List[ManagedChain]:
        return list(self._chains.values())

    def status(self) -> str:
        lines = [f"ChainManager @ {self.data_root}  ({len(self._chains)} chain(s))"]
        for c in self._chains.values():
            lines.append(c.status_line())
        return "\n".join(lines)
