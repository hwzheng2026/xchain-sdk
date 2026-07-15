"""Test MerkleTree operations."""
import pytest
from xchain.bridge_core import MerkleTree
from eth_utils import keccak


def test_empty_tree_root():
    """Empty tree should have a well-defined root (0x00..00)."""
    tree = MerkleTree([])
    root = tree.get_root()
    # Either 0x00...00 or 32 zero bytes
    assert root is not None
    assert len(root) == 32


def test_single_leaf_root():
    """A tree with one leaf has that leaf as its root."""
    leaf = keccak(b"hello")
    tree = MerkleTree([leaf])
    assert tree.get_root() == leaf


def test_two_leaf_root():
    """A tree with two leaves has a deterministic root."""
    a = keccak(b"a")
    b = keccak(b"b")
    tree = MerkleTree([a, b])
    expected = keccak(a + b)
    assert tree.get_root() == expected


def test_proof_verification():
    """A generated proof must verify against the root."""
    leaves = [keccak(f"event{i}".encode()) for i in range(8)]
    tree = MerkleTree(leaves)
    for i in range(len(leaves)):
        proof = tree.get_proof(i)
        leaf = tree.get_leaf(i)
        assert tree.verify(leaf, i, proof, tree.get_root())


def test_proof_rejects_tampered_leaf():
    """Proof must reject a leaf that's been tampered with."""
    leaves = [keccak(f"event{i}".encode()) for i in range(4)]
    tree = MerkleTree(leaves)
    proof = tree.get_proof(0)
    leaf = tree.get_leaf(0)
    tampered = keccak(b"evil")
    assert not tree.verify(tampered, 0, proof, tree.get_root())
