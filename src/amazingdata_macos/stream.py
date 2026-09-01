from __future__ import annotations

import json
from typing import Any

from .errors import GatewayResponseError, MissingStreamingDependency


class MarketStream:
    def __init__(self, client: Any, code_list: list[str], period: str):
        self.client = client
        self.code_list = list(dict.fromkeys(code_list))
        self.period = period
        self._connection: Any | None = None

    async def __aenter__(self) -> "MarketStream":
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:
            raise MissingStreamingDependency(
                "Install streaming support with: pip install 'amazingdata-macos[stream]'"
            ) from exc
        headers = {"X-API-Key": self.client.api_key} if self.client.api_key else None
        self._connection = await connect(
            self.client.websocket_url(),
            additional_headers=headers,
            open_timeout=self.client.timeout,
        )
        await self._connection.send(
            json.dumps({"code_list": self.code_list, "period": self.period})
        )
        response = json.loads(await self._connection.recv())
        if response.get("type") != "ack":
            await self._connection.close()
            self._connection = None
            raise GatewayResponseError(response.get("message", str(response)))
        return self

    async def __aexit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    def __aiter__(self) -> "MarketStream":
        return self

    async def __anext__(self) -> dict[str, Any]:
        if self._connection is None:
            raise StopAsyncIteration
        while True:
            message = json.loads(await self._connection.recv())
            if message.get("type") == "tick":
                return message["data"]
            if message.get("type") == "error":
                raise GatewayResponseError(message.get("message", "Streaming error"))

    async def add(self, *codes: str) -> None:
        await self._control("add", codes)

    async def remove(self, *codes: str) -> None:
        await self._control("remove", codes)

    async def _control(self, action: str, codes: tuple[str, ...]) -> None:
        if self._connection is None:
            raise GatewayResponseError("Market stream is not connected")
        await self._connection.send(
            json.dumps({"type": action, "code_list": list(codes)})
        )
        response = json.loads(await self._connection.recv())
        if response.get("type") != "ack":
            raise GatewayResponseError(response.get("message", str(response)))
