"""
Backward-compatibility shim.

The canonical module is `xchain` (matches the PyPI distribution name `xchain-sdk`
by convention `xchain` -> short, importable identifier). For users who naturally
write `import xchain_sdk` after seeing the distribution name, this shim re-exports
the public API.

Usage (both work after install):

    from xchain import XChainClient, message_hash
    # or
    from xchain_sdk import XChainClient, message_hash   # this shim
"""
from xchain import (  # noqa: F401
    __version__,
    XChainClient,
    ChainEndpoint,
    MessageType,
    message_hash,
    encode_message,
    chain,
    cli,
    client,
    bridge_core,
    contracts,
)

__all__ = [
    "__version__",
    "XChainClient",
    "ChainEndpoint",
    "MessageType",
    "message_hash",
    "encode_message",
    "chain",
    "cli",
    "client",
    "bridge_core",
    "contracts",
]
