from .client import Client
from .compat import BaseData, InfoData, MarketData, client, connect, login, subscribe
from .errors import (
    AmazingDataMacOSError,
    GatewayConnectionError,
    GatewayResponseError,
    MissingStreamingDependency,
)


__version__ = "0.1.0"
AmazingDataClient = Client

__all__ = [
    "AmazingDataClient",
    "AmazingDataMacOSError",
    "BaseData",
    "Client",
    "GatewayConnectionError",
    "GatewayResponseError",
    "InfoData",
    "MarketData",
    "MissingStreamingDependency",
    "client",
    "connect",
    "login",
    "subscribe",
]
