"""Contract artifacts loader and helpers."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, Optional

from web3 import Web3
from web3.contract import Contract


def load_artifact(name: str, artifacts_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load a contract artifact (ABI + bytecode) from the artifacts directory.

    Args:
        name: artifact file name, e.g. "compiled.json" or "LiquorCertificate.json"
        artifacts_path: directory containing artifacts. Defaults to
            ``<package>/contracts/artifacts``.

    Returns:
        Parsed JSON dict.
    """
    if artifacts_path is None:
        artifacts_path = Path(__file__).parent / "artifacts"
    path = Path(artifacts_path) / name
    with open(path) as f:
        return json.load(f)


def get_contract(w3: Web3, address: str, abi: list) -> Contract:
    """Create a Web3 contract instance with checksummed address.

    Args:
        w3: Web3 instance connected to the chain
        address: contract address (any case)
        abi: contract ABI list

    Returns:
        web3.contract.Contract instance
    """
    return w3.eth.contract(
        address=Web3.to_checksum_address(address),
        abi=abi,
    )
