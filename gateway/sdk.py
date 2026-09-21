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
        # 单席位账号：seat_held_by_other 表示名额被另一台机器（ECS live）占着，
        # 这属于「等一会儿就好」的状态，不是故障，看门狗不能因此重启进程。
        self.seat_held_by_other = False
        self.released_for_idle = False
        self.last_activity_at = 0.0
        self._last_login_attempt = 0.0
        # 正在处理（含排队等待 call_lock）的查询数：用于区分「客户端超时后请求仍在跑」
        # 与「网关真的空闲」，排查静默退出和请求堆积。
        self.inflight_queries = 0

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

    def _tgw_login(self, force_logout: bool) -> tuple[bool, bool]:
        """直接调用厂商原语登录，返回 ``(是否成功, 是否触顶)``。

        不走 ``AmazingData.login``：那层封装在登录失败时会自己 ``exit(0)``，
        而 ``exit`` 抛出的 ``SystemExit`` 会顺着调用栈把整个网关进程带走（这正是
        「静默退出、退出码 0、日志里什么都没有」的来源）。这里只做客户端想做的事，
        并且把 ``max_limitation`` 这个「账号名额已被别处占用」的信号带出来。
        """
        tgw_login = importlib.import_module("AmazingData.login.tgw_login")
        cfg, api_mode, spi = tgw_login.set_cfg(
            self.settings.tgw_user,
            self.settings.tgw_password,
            self.settings.tgw_host,
            self.settings.tgw_port,
            api_mode="kInternetMode",
            kColocationMode_para=None,
            force_logout=force_logout,
        )
        success = bool(tgw_login.tgw.Login(cfg, api_mode))
        return success, bool(getattr(spi, "max_limitation", False))

    def login(self) -> bool:
        if self.module is None:
            self.load()
        if self.module is None:
            return False
        with self.call_lock:
            with self._state_lock:
                self._last_login_attempt = time.time()
            try:
                success, limited = self._tgw_login(force_logout=False)
                if not success and limited:
                    if self.settings.force_logout:
                        # 显式开启才允许抢座；live 部署被踢会直接掉线，默认关。
                        LOG.warning(
                            "TGW seat is taken by another host; forcing logout because "
                            "AMAZINGDATA_FORCE_LOGOUT is enabled"
                        )
                        success, limited = self._tgw_login(force_logout=True)
                    else:
                        with self._state_lock:
                            self.ready = False
                            self.seat_held_by_other = True
                            self.last_error = (
                                "TGW seat is held by another host sharing this account; "
                                "waiting for it to be released"
                            )
                        LOG.warning(
                            "TGW seat is held by another host sharing this account; "
                            "waiting instead of forcing a logout (set "
                            "AMAZINGDATA_FORCE_LOGOUT=true to override)"
                        )
                        return False
                if not success:
                    self._fail(
                        "TGW login failed: server rejected the session "
                        f"(max_limitation={limited})"
                    )
                    LOG.error(self.last_error)
                    return False
                if self.settings.verify_login:
                    self._load_calendar()
                with self._state_lock:
                    now = time.time()
                    self.ready = True
                    self.login_started_at = now
                    self.last_success_at = now
                    self.last_error = ""
                    self.seat_held_by_other = False
                    self.released_for_idle = False
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
                # 「会不会服务这次请求」：空闲释放席位后进程仍可用，第一次查询会
                # 自己重新登录，所以客户端探活看的是 available 而不是 ready。
                "available": self.ready or self.released_for_idle,
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
                # 单席位账号的可观测性：是「故障」还是「在等 ECS 让座」。
                "seat_held_by_other": self.seat_held_by_other,
                "released_for_idle": self.released_for_idle,
                "idle_seconds": (
                    round(time.time() - self.last_activity_at, 1)
                    if self.last_activity_at
                    else None
                ),
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
        if self.module is None:
            raise SDKUnavailable(self.last_error or "AmazingData is not imported")
        if not self.ready and not self.ensure_session():
            raise SDKUnavailable(self.last_error or "TGW session is not ready")

        # calendar_end 是本网关的内部参数（兼容层用它转发构造 MarketData 时传入的
        # 日历上界），不能透传给厂商 SDK。
        params = dict(params)
        calendar_end = params.pop("calendar_end", None)
        required_end = self._requested_end_date(method, args, params, calendar_end)

        self.inflight_queries += 1
        requested_codes = args[0] if args and isinstance(args[0], list) else None
        started_at = time.time()
        LOG.info(
            "query start: %s.%s codes=%s inflight=%s",
            namespace,
            method,
            len(requested_codes) if requested_codes is not None else "-",
            self.inflight_queries,
        )
        try:
            with self.call_lock:
                waited = time.time() - started_at
                if waited > 1.0:
                    LOG.warning(
                        "query waited %.2fs for the SDK lock: %s.%s inflight=%s",
                        waited,
                        namespace,
                        method,
                        self.inflight_queries,
                    )
                instance = self._make_instance(namespace, required_end)
                function = getattr(instance, method, None)
                if function is None or not callable(function):
                    raise AttributeError(f"{namespace}.{method} does not exist")
                call_params = self._inject_defaults(function, params)
                try:
                    result = function(*args, **call_params)
                    with self._state_lock:
                        self.last_success_at = time.time()
                        self.last_activity_at = self.last_success_at
                        self.last_error = ""
                    return result
                except BaseException as exc:
                    self._fail(
                        f"{namespace}.{method} failed: {type(exc).__name__}: {exc}",
                        keep_ready=True,
                    )
                    raise RuntimeError(self.last_error) from exc
        finally:
            self.inflight_queries -= 1
            LOG.info(
                "query end: %s.%s elapsed=%.2fs inflight=%s",
                namespace,
                method,
                time.time() - started_at,
                self.inflight_queries,
            )

    def probe(self) -> bool:
        if not self.ready or self.module is None:
            return False
        with self.call_lock:
            for attempt in (1, 2):
                try:
                    self._load_calendar(force=True)
                    with self._state_lock:
                        self.last_success_at = time.time()
                        self.last_error = ""
                    return True
                except BaseException as exc:
                    # TGW 会在空闲一段时间后悄悄断掉会话，厂商 SDK 此时会返回
                    # None 并在其内部抛 TypeError（见 base_data.get_calendar）。
                    # 这属于可恢复状态：先重新登录再判死，避免网关无谓自杀。
                    LOG.warning(
                        "TGW watchdog probe failed (attempt %s): %s: %s",
                        attempt,
                        type(exc).__name__,
                        exc,
                    )
                    LOG.debug("probe failure detail", exc_info=True)
                    if attempt == 1 and self.login():
                        LOG.warning("TGW session re-login succeeded; retrying probe")
                        continue
                    self._fail(f"TGW probe failed: {type(exc).__name__}: {exc}")
                    return False
            return False

    def ensure_session(self) -> bool:
        """按需（重新）登录；两个尝试之间保持 ``seat_retry_seconds`` 间隔。

        客户端在冷启动或网关重启后第一次请求会走到这里。此时若是 ECS 占着席位，
        这里只会记一条日志并返回 False，由客户端的重试/等座逻辑接手。
        """
        with self._state_lock:
            if self.ready:
                return True
            now = time.time()
            if now - self._last_login_attempt < self.settings.seat_retry_seconds:
                return False
        return self.login()

    def release_session(self, reason: str) -> None:
        """主动让出席位，别让研究用的常驻进程一直占着唯一的座位。"""
        with self.call_lock:
            if self.module is not None:
                try:
                    self.module.logout(self.settings.tgw_user)
                except BaseException as exc:
                    LOG.warning(
                        "TGW logout failed: %s: %s", type(exc).__name__, exc
                    )
            with self._state_lock:
                self.ready = False
                self._calendar = None
                self._calendar_target = 0
                self.released_for_idle = True
                self.last_error = f"session released ({reason})"
            LOG.info("TGW session released (%s)", reason)

    def maybe_release_idle(self, *, busy: bool) -> bool:
        """空闲超过 ``idle_release_seconds`` 就释放席位；``busy`` 时永不释放。"""
        if self.settings.idle_release_seconds <= 0 or busy:
            return False
        with self._state_lock:
            if not self.ready:
                return False
            since = max(self.last_activity_at, self.login_started_at or 0.0)
            idle = time.time() - since
        if idle < self.settings.idle_release_seconds:
            return False
        self.release_session(f"idle {idle:.0f}s")
        return True

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
