"""Test bridge_core: message_hash, encode_message, merkle helpers."""
import pytest
from xchain.bridge_core import (
    message_hash, encode_message, MessageType, verify_single_leaf_merkle
)


def test_message_type_constants():
    assert MessageType.MINT == 0
    assert MessageType.BURN == 1
    assert MessageType.PLEDGE == 2
    assert MessageType.UNPLEDGE == 3


def test_message_hash_deterministic():
    """Same message must always produce the same hash."""
    msg = {
        "messageType": MessageType.MINT,
        "sourceChainId": 1001,
        "sourceBlockNumber": 12345,
        "sourceTxHash": "0x" + "ab" * 32,
        "sourceOwner": "0x" + "cd" * 20,
        "targetRecipient": "0x" + "ef" * 20,
        "sourceCertId": "0x" + "12" * 32,
        "targetCertId": "0x" + "34" * 32,
        "warehouseCode": "GZJJ-2024-001",
        "vintage": 2024,
        "valuation": 500000,
        "pledgeAmount": 0,
        "nonce": 1234567890,
        "merkleRoot": "0x" + "0" * 64,
        "leafIndex": 0,
    }
    h1 = message_hash(msg)
    h2 = message_hash(msg)
    assert h1 == h2
    assert isinstance(h1, str)
    assert h1.startswith("0x")
    assert len(h1) == 66  # 0x + 64 hex chars


def test_message_hash_changes_with_field():
    """Changing any field must change the hash."""
    base = {
        "messageType": MessageType.MINT,
        "sourceChainId": 1001,
        "sourceBlockNumber": 12345,
        "sourceTxHash": "0x" + "ab" * 32,
        "sourceOwner": "0x" + "cd" * 20,
        "targetRecipient": "0x" + "ef" * 20,
        "sourceCertId": "0x" + "12" * 32,
        "targetCertId": "0x" + "34" * 32,
        "warehouseCode": "GZJJ-2024-001",
        "vintage": 2024,
        "valuation": 500000,
        "pledgeAmount": 0,
        "nonce": 1234567890,
        "merkleRoot": "0x" + "0" * 64,
        "leafIndex": 0,
    }
    base_hash = message_hash(base)

    for field, new_val in [
        ("sourceChainId", 2001),
        ("sourceBlockNumber", 99999),
        ("vintage", 2025),
        ("valuation", 600000),
        ("nonce", 9999999),
        ("warehouseCode", "GZJJ-2025-002"),
    ]:
        m = dict(base)
        m[field] = new_val
        assert message_hash(m) != base_hash, f"Hash should differ when {field} changes"


def test_verify_single_leaf_merkle():
    """Single-leaf tree: root == leaf."""
    leaf = "0x" + "ab" * 32
    assert verify_single_leaf_merkle(leaf, leaf) is True
    other = "0x" + "cd" * 32
    assert verify_single_leaf_merkle(leaf, other) is False
