from __future__ import annotations

import importlib
import inspect
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .settings import Settings


LOG = logging.getLogger("amazingdata.gateway.sdk")
ALLOWED_NAMESPACES = {"BaseData", "InfoData", "MarketData"}

# A 股交易日历按北京时间；容器默认 UTC，直接用 datetime.now() 会在凌晨 00:00-08:00
# （CST）退回前一天，导致日历少一天。
CHINA_TZ = timezone(timedelta(hours=8))

# BaseData.get_calendar(date=...) 的 date 决定日历上界：日历只覆盖 <= date 的交易日。
# 该 SDK 把 date 的默认值在模块导入时求值一次（见 AmazingData/query_api/base_data.py
# 类体里的 datetime_to_int()），于是"不传 date"会永久冻结在网关进程启动当天。
CALENDAR_MARKET = "SH"


def trading_day_int() -> int:
    """当前北京日期 YYYYMMDD；网关日历至少要覆盖到这一天。"""
    return int(datetime.now(CHINA_TZ).strftime("%Y%m%d"))


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
        # 上次取日历时使用的 date 上界；用它判断"是否需要重新取"，避免每个请求
        # 都打一次 TGW，也避免周末（日历末位<今天）反复刷新。
        self._calendar_target = 0
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
                    self._load_calendar()
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
            calendar_last_day = (
                int(max(self._calendar)) if self._calendar else None
            )
            today = trading_day_int()
            return {
                "status": "ready" if self.ready else "degraded",
                "ready": self.ready,
                "sdk_imported": self.module is not None,
                "sdk_version": self.sdk_version,
                "credentials_configured": self.settings.credentials_complete,
                "login_started_at": self.login_started_at,
                "last_success_at": self.last_success_at,
                "last_error": self.last_error,
                # 日历覆盖范围由 SDK 的 date 参数决定，缺失会让 K 线查询静默少几天。
                "trading_day": today,
                "calendar_target": self._calendar_target,
                "calendar_last_day": calendar_last_day,
                "calendar_stale": self._calendar_target < today,
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

        # calendar_end 是本网关的内部参数（兼容层用它转发构造 MarketData 时传入的
        # 日历上界），不能透传给厂商 SDK。
        params = dict(params)
        calendar_end = params.pop("calendar_end", None)
        required_end = self._requested_end_date(method, args, params, calendar_end)

        with self.call_lock:
            instance = self._make_instance(namespace, required_end)
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
                self._load_calendar(force=True)
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

    def _make_instance(self, namespace: str, required_end: int | None = None) -> Any:
        assert self.module is not None
        if namespace == "MarketData":
            return self.module.MarketData(self._load_calendar(required_end))
        return getattr(self.module, namespace)()

    @staticmethod
    def _requested_end_date(
        method: str,
        args: list[Any],
        params: dict[str, Any],
        calendar_end: Any = None,
    ) -> int | None:
        """取本次请求要求覆盖到的日期上界（query_kline 的 end_date 或调用方日历末位）。"""
        candidates: list[Any] = []
        if method == "query_kline":
            candidates.append(params.get("end_date"))
            if len(args) >= 3:
                candidates.append(args[2])
        candidates.append(calendar_end)
        values = []
        for value in candidates:
            if value is None or isinstance(value, bool):
                continue
            try:
                values.append(int(value))
            except (TypeError, ValueError):
                continue
        return max(values) if values else None

    def _load_calendar(
        self, required_end: int | None = None, force: bool = False
    ) -> Any:
        """返回覆盖到 ``max(今天, required_end)`` 的交易日历。

        ``BaseData.get_calendar`` 的 ``date`` 默认值在厂商 SDK 导入时就被固化，
        省略 ``date`` 会让日历永久停在网关进程启动当天，K 线查询随之静默截断。
        这里始终显式传入 date，并且只在覆盖范围不足时才重新请求。
        """
        assert self.module is not None
        with self._state_lock:
            cached = self._calendar
            covered = self._calendar_target

        target = trading_day_int()
        if required_end is not None and required_end > target:
            target = required_end
        # 强制刷新（watchdog 探活）只用来确认 TGW 仍在响应，不能把已经扩展过的
        # 覆盖范围缩回去，否则每个探活周期后都要为同一个 end_date 再打一次 TGW。
        if force and covered > target:
            target = covered

        if not force and cached is not None and covered >= target:
            return cached

        calendar = self.module.BaseData().get_calendar("str", CALENDAR_MARKET, target)
        if not calendar:
            raise RuntimeError(f"get_calendar returned no trading days for {target}")

        with self._state_lock:
            self._calendar = calendar
            self._calendar_target = target
        LOG.info(
            "Trading calendar refreshed: target=%s last_day=%s days=%s",
            target,
            max(calendar),
            len(calendar),
        )
        return calendar

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
