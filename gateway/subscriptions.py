from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict
from typing import Any

from .sdk import SDKManager, SDKUnavailable
from .serialization import to_jsonable
from .settings import Settings


LOG = logging.getLogger("amazingdata.gateway.subscriptions")


class SubscriptionManager:
    def __init__(self, sdk: SDKManager, settings: Settings):
        self.sdk = sdk
        self.settings = settings
        self._lock = threading.RLock()
        self._instance: Any | None = None
        self._thread: threading.Thread | None = None
        self._codes: set[str] = set()
        self._period = ""
        self._queues: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._loops: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self.last_error = ""

    @property
    def codes(self) -> list[str]:
        with self._lock:
            return sorted(self._codes)

    @property
    def period(self) -> str:
        with self._lock:
            return self._period

    def ensure_started(
        self,
        requested_codes: list[str],
        requested_period: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        if not self.sdk.ready or self.sdk.module is None:
            raise SDKUnavailable(self.sdk.last_error or "TGW session is not ready")
        clean_codes = sorted({code for code in requested_codes if code})
        if not clean_codes:
            raise ValueError("code_list cannot be empty")

        with self._lock:
            if self._instance is not None:
                if requested_period != self._period:
                    raise ValueError(
                        f"Subscription period is already {self._period}, "
                        f"requested {requested_period}"
                    )
                missing = sorted(set(clean_codes) - self._codes)
                if missing:
                    raise ValueError(
                        "The vendor SDK cannot expand a live subscription safely. "
                        f"Restart the gateway with these codes preconfigured: {missing}"
                    )
                return

            global_codes = sorted(set(self.settings.subscribe_codes) or set(clean_codes))
            missing = sorted(set(clean_codes) - set(global_codes))
            if missing:
                raise ValueError(
                    f"Requested codes are not in AMAZINGDATA_SUBSCRIBE_CODES: {missing}"
                )
            configured_period = self.settings.subscribe_period or requested_period
            if self.settings.subscribe_codes and requested_period != configured_period:
                raise ValueError(
                    f"Gateway subscription period is configured as {configured_period}"
                )

            module = self.sdk.module
            period_value = self.sdk.period_value(configured_period)
            instance = module.SubscribeData()

            @instance.register(code_list=global_codes, period=period_value)
            def on_tick(data: Any, _period: Any) -> None:
                self._publish(data)

            self._instance = instance
            self._codes = set(global_codes)
            self._period = configured_period
            self._thread = threading.Thread(
                target=self._run,
                args=(instance,),
                daemon=True,
                name="amazingdata-subscription",
            )
            self._thread.start()
            LOG.info(
                "TGW subscription started: period=%s codes=%s",
                configured_period,
                global_codes,
            )

    def attach(
        self,
        queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
        codes: list[str],
    ) -> None:
        with self._lock:
            self._loops[queue] = loop
            for code in codes:
                if code not in self._codes:
                    raise ValueError(f"Code is not in the global subscription: {code}")
                self._queues[code].add(queue)

    def detach(self, queue: asyncio.Queue, codes: list[str] | None = None) -> None:
        with self._lock:
            targets = codes if codes is not None else list(self._queues)
            for code in targets:
                queues = self._queues.get(code)
                if queues is None:
                    continue
                queues.discard(queue)
                if not queues:
                    self._queues.pop(code, None)
            if codes is None:
                self._loops.pop(queue, None)

    def _run(self, instance: Any) -> None:
        try:
            instance.run()
        except BaseException as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            LOG.exception("TGW subscription thread stopped")

    def _publish(self, data: Any) -> None:
        try:
            payload = to_jsonable(data)
            code = getattr(data, "code", None)
            if code is None and isinstance(payload, dict):
                code = payload.get("code") or payload.get("CODE")
            if not code:
                return
            with self._lock:
                recipients = [
                    (queue, self._loops.get(queue))
                    for queue in self._queues.get(str(code), set())
                ]
            for queue, loop in recipients:
                if loop is not None and not loop.is_closed():
                    loop.call_soon_threadsafe(self._put, queue, payload)
        except BaseException:
            LOG.exception("Unable to publish TGW tick")

    @staticmethod
    def _put(queue: asyncio.Queue, payload: Any) -> None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass
