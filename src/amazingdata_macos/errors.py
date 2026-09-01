class AmazingDataMacOSError(RuntimeError):
    """Base exception for the native macOS client."""


class GatewayConnectionError(AmazingDataMacOSError):
    """The local gateway could not be reached."""


class GatewayResponseError(AmazingDataMacOSError):
    """The gateway or vendor SDK rejected a request."""


class MissingStreamingDependency(AmazingDataMacOSError):
    """The optional WebSocket dependency is not installed."""
