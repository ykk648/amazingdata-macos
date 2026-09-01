from __future__ import annotations

import importlib
import inspect
import logging
import os
import threading
import time
from typing import Any

from .settings import Settings


LOG = logging.getLogger("amazingdata.gateway.sdk")
ALLOWED_NAMESPACES = {"BaseData", "InfoData", "MarketData"}


class SDKUnavailable(RuntimeError):
    pass


class SDKManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.module: Any | None = None
        self.ready = False
        self.login_started_at: float | None = None
        self.last_success_at: float | None = None
        self.last_error = ""
        self.sdk_version = "unknown"
        self._calendar: Any | None = None
        self._state_lock = threading.RLock()
        self.call_lock = threading.RLock()

    def load(self) -> None:
        try:
            self.module = importlib.import_module("AmazingData")
            self.sdk_version = str(
                getattr(self.module, "__version__", "unknown")
            )
            os.makedirs(self.settings.local_data, exist_ok=True)
            os.makedirs(self.settings.state_dir, exist_ok=True)
        except BaseException as exc:
            self._fail(f"AmazingData import failed: {type(exc).__name__}: {exc}")
            LOG.exception("Unable to import AmazingData")

    def start(self) -> bool:
        self.load()
        if self.module is None:
            return False
        if not self.settings.credentials_complete:
            self._fail("TGW credentials are incomplete; edit .env and restart")
            LOG.warning(self.last_error)
            return False
        return self.login()

    def login(self) -> bool:
        if self.module is None:
            self.load()
        if self.module is None:
            return False
        with self.call_lock:
            try:
                self.module.login(
                    username=self.settings.tgw_user,
                    password=self.settings.tgw_password,
                    host=self.settings.tgw_host,
                    port=self.settings.tgw_port,
                )
                if self.settings.verify_login:
                    self._calendar = self.module.BaseData().get_calendar()
                with self._state_lock:
                    now = time.time()
                    self.ready = True
                    self.login_started_at = now
                    self.last_success_at = now
                    self.last_error = ""
                LOG.info("TGW login completed and session is ready")
                return True
            except BaseException as exc:
                self._fail(f"TGW login failed: {type(exc).__name__}: {exc}")
                LOG.exception("TGW login failed")
                return False

    def health(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "status": "ready" if self.ready else "degraded",
                "ready": self.ready,
                "sdk_imported": self.module is not None,
                "sdk_version": self.sdk_version,
                "credentials_configured": self.settings.credentials_complete,
                "login_started_at": self.login_started_at,
                "last_success_at": self.last_success_at,
                "last_error": self.last_error,
            }

    def invoke(
        self,
        namespace: str,
        method: str,
        args: list[Any],
        params: dict[str, Any],
    ) -> Any:
        if namespace not in ALLOWED_NAMESPACES:
            raise ValueError(f"Unsupported SDK class: {namespace}")
        if not method.isidentifier() or method.startswith("_"):
            raise ValueError(f"Invalid SDK method: {method}")
        if not self.ready or self.module is None:
            raise SDKUnavailable(self.last_error or "TGW session is not ready")

        with self.call_lock:
            instance = self._make_instance(namespace)
            function = getattr(instance, method, None)
            if function is None or not callable(function):
                raise AttributeError(f"{namespace}.{method} does not exist")
            call_params = self._inject_defaults(function, params)
            try:
                result = function(*args, **call_params)
                with self._state_lock:
                    self.last_success_at = time.time()
                    self.last_error = ""
                return result
            except BaseException as exc:
                self._fail(
                    f"{namespace}.{method} failed: {type(exc).__name__}: {exc}",
                    keep_ready=True,
                )
                raise RuntimeError(self.last_error) from exc

    def probe(self) -> bool:
        if not self.ready or self.module is None:
            return False
        with self.call_lock:
            try:
                self._calendar = self.module.BaseData().get_calendar()
                with self._state_lock:
                    self.last_success_at = time.time()
                    self.last_error = ""
                return True
            except BaseException as exc:
                self._fail(f"TGW probe failed: {type(exc).__name__}: {exc}")
                LOG.exception("TGW watchdog probe failed")
                return False

    def schema(self) -> dict[str, list[dict[str, str]]]:
        if self.module is None:
            raise SDKUnavailable(self.last_error or "AmazingData is not imported")
        result: dict[str, list[dict[str, str]]] = {}
        for namespace in sorted(ALLOWED_NAMESPACES):
            try:
                instance = self._make_instance(namespace)
            except BaseException:
                result[namespace] = []
                continue
            methods = []
            for name in sorted(dir(instance)):
                if name.startswith("_"):
                    continue
                function = getattr(instance, name, None)
                if not callable(function):
                    continue
                try:
                    signature = str(inspect.signature(function))
                except (TypeError, ValueError):
                    signature = "(...)"
                methods.append({"name": name, "signature": signature})
            result[namespace] = methods
        return result

    def _make_instance(self, namespace: str) -> Any:
        assert self.module is not None
        if namespace == "MarketData":
            if self._calendar is None:
                self._calendar = self.module.BaseData().get_calendar()
            return self.module.MarketData(self._calendar)
        return getattr(self.module, namespace)()

    def _inject_defaults(
        self, function: Any, params: dict[str, Any]
    ) -> dict[str, Any]:
        result = dict(params)
        try:
            signature = inspect.signature(function).parameters
        except (TypeError, ValueError):
            return result
        if "local_path" in signature and "local_path" not in result:
            result["local_path"] = os.path.join(self.settings.local_data, "")
        if "is_local" in signature and "is_local" not in result:
            result["is_local"] = False
        if "period" in result and isinstance(result["period"], str):
            result["period"] = self.period_value(result["period"])
        return result

    def period_value(self, name: str) -> Any:
        if self.module is None:
            raise SDKUnavailable("AmazingData is not imported")
        try:
            value = getattr(self.module.constant.Period, name)
            return getattr(value, "value", value)
        except AttributeError as exc:
            raise ValueError(f"Unknown period: {name}") from exc

    def _fail(self, message: str, keep_ready: bool = False) -> None:
        with self._state_lock:
            if not keep_ready:
                self.ready = False
            self.last_error = message
