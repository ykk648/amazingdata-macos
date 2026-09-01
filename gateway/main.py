from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .sdk import SDKManager, SDKUnavailable
from .serialization import count_rows, to_jsonable
from .settings import Settings
from .subscriptions import SubscriptionManager


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOG = logging.getLogger("amazingdata.gateway")

SETTINGS = Settings.from_env()
SDK = SDKManager(SETTINGS)
SUBSCRIPTIONS = SubscriptionManager(SDK, SETTINGS)


class QueryRequest(BaseModel):
    namespace: str
    method: str
    args: list[Any] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


async def _watchdog() -> None:
    failures = 0
    while True:
        await asyncio.sleep(SETTINGS.watchdog_interval)
        if not SETTINGS.credentials_complete:
            continue
        ok = await asyncio.to_thread(SDK.probe)
        failures = 0 if ok else failures + 1
        if failures >= SETTINGS.watchdog_failures:
            LOG.critical(
                "TGW session failed %s probes; exiting for Docker restart",
                failures,
            )
            os._exit(75)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await asyncio.to_thread(SDK.start)
    watchdog_task = asyncio.create_task(_watchdog())
    try:
        yield
    finally:
        watchdog_task.cancel()


app = FastAPI(
    title="AmazingData macOS Gateway",
    version="0.1.0",
    lifespan=lifespan,
)


def _authorized(value: str | None) -> bool:
    return not SETTINGS.api_key or (
        value is not None and hmac.compare_digest(value, SETTINGS.api_key)
    )


def _require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not _authorized(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "name": "AmazingData macOS Gateway",
        "version": app.version,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health/live")
def health_live() -> dict[str, bool]:
    return {"alive": True}


@app.get("/health")
def health(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    _require_api_key(x_api_key)
    data = SDK.health()
    data["subscription_codes"] = SUBSCRIPTIONS.codes
    data["subscription_period"] = SUBSCRIPTIONS.period
    data["subscription_error"] = SUBSCRIPTIONS.last_error
    return data


@app.get("/health/ready")
def health_ready(x_api_key: str | None = Header(default=None)) -> JSONResponse:
    _require_api_key(x_api_key)
    data = SDK.health()
    return JSONResponse(status_code=200 if SDK.ready else 503, content=data)


@app.get("/v1/schema")
def schema(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    _require_api_key(x_api_key)
    try:
        return {"ok": True, "namespaces": SDK.schema()}
    except SDKUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/v1/query")
async def query(
    request: QueryRequest,
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    _require_api_key(x_api_key)
    try:
        result = await asyncio.to_thread(
            SDK.invoke,
            request.namespace,
            request.method,
            request.args,
            request.params,
        )
        return JSONResponse(
            content={
                "ok": True,
                "rows": count_rows(result),
                "data": to_jsonable(result),
            }
        )
    except SDKUnavailable as exc:
        return JSONResponse(
            status_code=503, content={"ok": False, "error": str(exc)}
        )
    except (ValueError, AttributeError) as exc:
        return JSONResponse(
            status_code=400, content={"ok": False, "error": str(exc)}
        )
    except Exception as exc:
        LOG.exception("SDK query failed")
        return JSONResponse(
            status_code=502, content={"ok": False, "error": str(exc)}
        )


@app.websocket("/ws/market")
async def market(websocket: WebSocket) -> None:
    api_key = websocket.query_params.get("api_key") or websocket.headers.get(
        "x-api-key"
    )
    if not _authorized(api_key):
        await websocket.close(code=4401, reason="Invalid API key")
        return

    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    attached_codes: set[str] = set()
    receiver: asyncio.Task | None = None
    try:
        config = await websocket.receive_json()
        codes = [str(code) for code in config.get("code_list", []) if code]
        period = str(config.get("period", "snapshot"))
        loop = asyncio.get_running_loop()
        await asyncio.to_thread(
            SUBSCRIPTIONS.ensure_started, codes, period, loop
        )
        SUBSCRIPTIONS.attach(queue, loop, codes)
        attached_codes.update(codes)
        await websocket.send_json(
            {
                "type": "ack",
                "action": "subscribe",
                "codes": sorted(attached_codes),
                "global_codes": SUBSCRIPTIONS.codes,
                "period": SUBSCRIPTIONS.period,
            }
        )

        async def receive_commands() -> None:
            while True:
                command = await websocket.receive_json()
                action = command.get("type")
                command_codes = {
                    str(code) for code in command.get("code_list", []) if code
                }
                if action == "add":
                    missing = command_codes - set(SUBSCRIPTIONS.codes)
                    if missing:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": (
                                    "Restart the gateway with the missing codes in "
                                    f"AMAZINGDATA_SUBSCRIBE_CODES: {sorted(missing)}"
                                ),
                            }
                        )
                        continue
                    SUBSCRIPTIONS.attach(queue, loop, sorted(command_codes))
                    attached_codes.update(command_codes)
                    await websocket.send_json(
                        {
                            "type": "ack",
                            "action": "add",
                            "added": sorted(command_codes),
                        }
                    )
                elif action == "remove":
                    removed = command_codes & attached_codes
                    SUBSCRIPTIONS.detach(queue, sorted(removed))
                    attached_codes.difference_update(removed)
                    await websocket.send_json(
                        {
                            "type": "ack",
                            "action": "remove",
                            "removed": sorted(removed),
                        }
                    )
                else:
                    await websocket.send_json(
                        {"type": "error", "message": f"Unknown action: {action}"}
                    )

        receiver = asyncio.create_task(receive_commands())
        while True:
            tick_task = asyncio.create_task(queue.get())
            done, pending = await asyncio.wait(
                {tick_task, receiver}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                if task is not receiver:
                    task.cancel()
            if receiver in done:
                break
            payload = tick_task.result()
            await websocket.send_json({"type": "tick", "data": payload})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        LOG.exception("WebSocket subscription failed")
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if receiver is not None:
            receiver.cancel()
        SUBSCRIPTIONS.detach(queue)
