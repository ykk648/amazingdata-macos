from __future__ import annotations

from typing import Any

from .client import Client, RemoteNamespace


_DEFAULT_CLIENT = Client()


def connect(
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    global _DEFAULT_CLIENT
    _DEFAULT_CLIENT = Client(base_url=base_url, api_key=api_key, timeout=timeout)
    return _DEFAULT_CLIENT.health()


def login(
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = 180.0,
    **vendor_credentials: Any,
) -> bool:
    if vendor_credentials:
        names = ", ".join(sorted(vendor_credentials))
        raise TypeError(
            f"Vendor credentials ({names}) belong in the gateway .env file. "
            "Call login() without them from macOS."
        )
    return bool(connect(base_url, api_key, timeout).get("ready"))


class BaseData(RemoteNamespace):
    def __init__(self):
        super().__init__(_DEFAULT_CLIENT, "BaseData")


class InfoData(RemoteNamespace):
    def __init__(self):
        super().__init__(_DEFAULT_CLIENT, "InfoData")


class MarketData(RemoteNamespace):
    def __init__(self, _calendar: Any = None):
        super().__init__(_DEFAULT_CLIENT, "MarketData")


def client() -> Client:
    return _DEFAULT_CLIENT


def subscribe(code_list: list[str], period: str = "snapshot"):
    return _DEFAULT_CLIENT.stream(code_list, period)
