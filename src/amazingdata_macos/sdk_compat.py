"""Compatibility layer for code written against the official AmazingData SDK.

This module is intentionally imported explicitly on macOS instead of shadowing
the vendor's ``AmazingData`` package. Windows and Linux can continue importing
the vendor package directly, while macOS forwards the same small API surface to
the local gateway.
"""

from __future__ import annotations

from enum import Enum
from types import SimpleNamespace
from typing import Any

import pandas as pd

from .client import Client


class Period(Enum):
    min1 = "min1"
    min3 = "min3"
    min5 = "min5"
    min10 = "min10"
    min15 = "min15"
    min30 = "min30"
    min60 = "min60"
    min120 = "min120"
    day = "day"


constant = SimpleNamespace(Period=Period)


def _records_to_frame(value: Any) -> Any:
    if isinstance(value, list):
        return pd.DataFrame(value)
    if isinstance(value, dict):
        return {key: _records_to_frame(item) for key, item in value.items()}
    return value


def _period_name(value: Any) -> str:
    if isinstance(value, Period):
        return value.value
    if hasattr(value, "value"):
        value = value.value
    return str(value)


class _RemoteSDK:
    namespace: str

    def __init__(self, client: Client | None = None):
        self._client = client or _default_client

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        return self._client.query(self.namespace, method, args=list(args), **kwargs)


class BaseData(_RemoteSDK):
    namespace = "BaseData"

    def get_calendar(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("get_calendar", *args, **kwargs)

    def get_backward_factor(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        # The official SDK keeps this cache on the caller's filesystem. The
        # gateway owns the corresponding cache inside its container instead.
        kwargs.pop("local_path", None)
        refresh = kwargs.pop("refresh", None)
        if refresh is not None and "is_local" not in kwargs:
            kwargs["is_local"] = not bool(refresh)
        result = self._call("get_backward_factor", *args, **kwargs)
        if isinstance(result, pd.DataFrame):
            return result
        if isinstance(result, list):
            return pd.DataFrame(result)
        raise TypeError("Gateway returned an unexpected backward-factor payload")

    def __getattr__(self, method: str):
        if method.startswith("_"):
            raise AttributeError(method)

        def call(*args: Any, **kwargs: Any) -> Any:
            return self._call(method, *args, **kwargs)

        return call


class InfoData(_RemoteSDK):
    namespace = "InfoData"

    def get_fund_nav(self, *args: Any, **kwargs: Any) -> dict[str, pd.DataFrame]:
        # See BaseData.get_backward_factor: host paths are not valid in Docker.
        kwargs.pop("local_path", None)
        result = self._call("get_fund_nav", *args, **kwargs)
        if not isinstance(result, dict):
            raise TypeError("Gateway returned an unexpected fund-NAV payload")
        return _records_to_frame(result)

    def __getattr__(self, method: str):
        if method.startswith("_"):
            raise AttributeError(method)

        def call(*args: Any, **kwargs: Any) -> Any:
            return self._call(method, *args, **kwargs)

        return call


class MarketData(_RemoteSDK):
    namespace = "MarketData"

    def __init__(self, _calendar: Any = None, client: Client | None = None):
        super().__init__(client)

    def query_kline(
        self,
        code_list: list[str],
        begin_date: int,
        end_date: int,
        period: Any,
        **kwargs: Any,
    ) -> dict[str, pd.DataFrame]:
        result = self._call(
            "query_kline",
            code_list=code_list,
            begin_date=begin_date,
            end_date=end_date,
            period=_period_name(period),
            **kwargs,
        )
        if not isinstance(result, dict):
            raise TypeError("Gateway returned an unexpected K-line payload")
        return _records_to_frame(result)

    def __getattr__(self, method: str):
        if method.startswith("_"):
            raise AttributeError(method)

        def call(*args: Any, **kwargs: Any) -> Any:
            return self._call(method, *args, **kwargs)

        return call


_default_client = Client()


def connect(
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    global _default_client
    _default_client = Client(base_url=base_url, api_key=api_key, timeout=timeout)
    return _default_client.health()


def login(*_args: Any, **_kwargs: Any) -> bool:
    """Verify the local gateway instead of accepting vendor credentials on macOS."""
    return bool(connect().get("ready"))


def client() -> Client:
    return _default_client


def subscribe(code_list: list[str], period: str = "snapshot"):
    return _default_client.stream(code_list, period)
