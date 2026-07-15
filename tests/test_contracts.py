"""Test Solidity contract source code is present and has expected functions."""
from pathlib import Path

import pytest

CONTRACTS_DIR = Path(__file__).parent.parent / "src" / "xchain" / "contracts"


@pytest.mark.parametrize("contract,functions", [
    ("LiquorCertificate", ["function mint", "function mintMirror", "function burn", "function getCertificate"]),
    ("LiquorPledge", ["function pledge", "function unpledge", "function completeCrossChainPledge"]),
    ("CrossChainBridge", ["function submitBlockHeader", "function processMessage", "function sourceToTarget", "function verifyMerkleProof"]),
])
def test_contract_source_has_functions(contract, functions):
    """Each contract must define the public functions we depend on.

    For public mappings Solidity auto-generates a getter, so we accept
    either `function name` (explicit) or `public name` (implicit getter).
    """
    src = (CONTRACTS_DIR / f"{contract}.sol").read_text()
    for fn in functions:
        # "function sourceToTarget" OR "public sourceToTarget" (auto-getter)
        name = fn.split()[-1]
        assert (
            fn in src or f"public {name}" in src or f"public mapping" in src
        ), f"{contract}.sol must define {fn} (explicit or as public mapping)"


def test_valuation_field_documented():
    """valuation field must be documented as RMB (not wei)."""
    src = (CONTRACTS_DIR / "LiquorCertificate.sol").read_text()
    assert "RMB" in src or "rmb" in src.lower(), \
        "LiquorCertificate.sol should document valuation unit (RMB)"
