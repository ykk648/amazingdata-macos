from __future__ import annotations

import json
import os
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .errors import GatewayConnectionError, GatewayResponseError


_DATAFRAME_MARKER = "__amazingdata_macos_type__"


def _restore_jsonable(value: Any) -> Any:
    if isinstance(value, list):
        return [_restore_jsonable(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get(_DATAFRAME_MARKER) == "dataframe":
        try:
            import pandas as pd

            return pd.DataFrame(
                _restore_jsonable(value["data"]),
                index=_restore_jsonable(value["index"]),
                columns=value["columns"],
            )
        except (ImportError, KeyError, TypeError, ValueError) as exc:
            raise GatewayResponseError("Gateway returned an invalid DataFrame payload") from exc
    return {key: _restore_jsonable(item) for key, item in value.items()}


class Client:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 180.0,
    ):
        self.base_url = (
            base_url
            or os.getenv("AMAZINGDATA_GATEWAY_URL")
            or "http://127.0.0.1:8765"
        ).rstrip("/")
        self.api_key = (
            api_key if api_key is not None else os.getenv("AMAZINGDATA_API_KEY", "")
        )
        self.timeout = timeout
        self.base_data = RemoteNamespace(self, "BaseData")
        self.info_data = RemoteNamespace(self, "InfoData")
        self.market_data = RemoteNamespace(self, "MarketData")

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def schema(self) -> dict[str, Any]:
        return self._request("GET", "/v1/schema")

    def query(
        self,
        namespace: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        raw: bool = False,
        args: list[Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if params is not None and kwargs:
            raise TypeError("Use either params or keyword arguments, not both")
        payload = {
            "namespace": namespace,
            "method": method,
            "args": list(args or []),
            "params": dict(params or kwargs),
        }
        envelope = self._request("POST", "/v1/query", payload)
        if not envelope.get("ok", False):
            raise GatewayResponseError(envelope.get("error", "Unknown gateway error"))
        return envelope if raw else _restore_jsonable(envelope.get("data"))

    def stream(self, code_list: list[str], period: str = "snapshot") -> "MarketStream":
        from .stream import MarketStream

        return MarketStream(self, code_list, period)

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                data = json.loads(exc.read().decode("utf-8"))
                message = data.get("error") or data.get("detail") or str(data)
            except Exception:
                message = str(exc)
            raise GatewayResponseError(
                f"Gateway returned HTTP {exc.code}: {message}"
            ) from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise GatewayConnectionError(
                f"Cannot reach AmazingData gateway at {self.base_url}: {exc}"
            ) from exc

    def websocket_url(self) -> str:
        parts = urlsplit(self.base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        query = f"api_key={quote(self.api_key)}" if self.api_key else ""
        return urlunsplit((scheme, parts.netloc, "/ws/market", query, ""))


class RemoteNamespace:
    def __init__(self, client: Client, namespace: str):
        self._client = client
        self._namespace = namespace

    def __getattr__(self, method: str):
        if method.startswith("_") or not method.isidentifier():
            raise AttributeError(method)

        def remote_call(*args: Any, **params: Any) -> Any:
            return self._client.query(
                self._namespace, method, params, args=list(args)
            )

        remote_call.__name__ = method
        remote_call.__qualname__ = f"{self._namespace}.{method}"
        return remote_call
