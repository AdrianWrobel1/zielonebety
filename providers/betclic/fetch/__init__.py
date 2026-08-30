"""
Betclic Fetch Submodule Package
"""

from providers.betclic.fetch.fetcher import BetclicFetcher
from providers.betclic.fetch.grpc_client import BetclicGrpcClient, BetclicGrpcParser

__all__ = ["BetclicFetcher", "BetclicGrpcClient", "BetclicGrpcParser"]
