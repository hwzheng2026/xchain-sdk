"""Verify the xchain_sdk shim module exposes the same public API as xchain.

Both import paths must work; both must agree on the canonical objects.
"""
import importlib
import sys


def _fresh_import(modname):
    """Import a module fresh, ignoring any cached version."""
    for cached in [k for k in list(sys.modules) if k == modname or k.startswith(modname + ".")]:
        del sys.modules[cached]
    return importlib.import_module(modname)


def test_xchain_imports():
    xchain = _fresh_import("xchain")
    assert xchain.__version__ == "0.1.0"
    assert hasattr(xchain, "XChainClient")
    assert hasattr(xchain, "message_hash")


def test_xchain_sdk_shim_imports():
    xchain_sdk = _fresh_import("xchain_sdk")
    assert xchain_sdk.__version__ == "0.1.0"
    # Same canonical class object, not just a copy.
    from xchain import XChainClient as XCCanonical
    assert xchain_sdk.XChainClient is XCCanonical


def test_both_apis_are_identical():
    xchain = _fresh_import("xchain")
    xchain_sdk = _fresh_import("xchain_sdk")

    canonical_public = {n for n in dir(xchain) if not n.startswith("_")}
    shim_public = {n for n in dir(xchain_sdk) if not n.startswith("_")}

    # Shim should expose at least the same public surface.
    missing = canonical_public - shim_public
    assert not missing, f"shim missing: {missing}"
