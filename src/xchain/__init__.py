"""Top-level xchain package."""
from .client import XChainClient, ChainEndpoint
from .bridge_core import message_hash, encode_message, MessageType
from . import chain, contracts, cli

__version__ = "0.1.0"
__all__ = [
    "XChainClient",
    "ChainEndpoint",
    "message_hash",
    "encode_message",
    "MessageType",
    "chain",
    "contracts",
    "cli",
]
