from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    tgw_user: str
    tgw_password: str
    tgw_host: str
    tgw_port: int
    api_key: str
    verify_login: bool
    local_data: str
    state_dir: str
    watchdog_interval: int
    watchdog_failures: int
    subscribe_codes: tuple[str, ...]
    subscribe_period: str
    # TGW 账号是单席位：本机与 ECS live 部署共用同一账号时，厂商 login 默认会用
    # force_logout 把对方踢下线（被踢的一方原生库会直接结束进程）。研究侧默认
    # 只「排队等座」不抢座，live 侧因此永远不会被本机踢掉。
    force_logout: bool = False
    # 空闲多久主动释放席位（秒，0 = 不释放）。一直占着座位会逼 live 部署来抢。
    idle_release_seconds: int = 600
    # 抢不到座位时的重试间隔（秒），避免高频打 TGW 登录接口。
    seat_retry_seconds: int = 60

    @property
    def credentials_complete(self) -> bool:
        return all((self.tgw_user, self.tgw_password, self.tgw_host))

    @classmethod
    def from_env(cls) -> "Settings":
        codes = tuple(
            dict.fromkeys(
                code.strip()
                for code in os.getenv("AMAZINGDATA_SUBSCRIBE_CODES", "").split(",")
                if code.strip()
            )
        )
        return cls(
            tgw_user=os.getenv("TGW_USER", "").strip(),
            tgw_password=os.getenv("TGW_PASSWORD", ""),
            tgw_host=os.getenv("TGW_HOST", "").strip(),
            tgw_port=_as_int(os.getenv("TGW_PORT"), 8600),
            api_key=os.getenv("AMAZINGDATA_API_KEY", ""),
            verify_login=_as_bool(os.getenv("AMAZINGDATA_VERIFY_LOGIN"), True),
            local_data=os.getenv("AMAZINGDATA_LOCAL_DATA", "/data"),
            state_dir=os.getenv("AMAZINGDATA_STATE_DIR", "/state"),
            watchdog_interval=max(
                30, _as_int(os.getenv("AMAZINGDATA_WATCHDOG_INTERVAL"), 180)
            ),
            watchdog_failures=max(
                1, _as_int(os.getenv("AMAZINGDATA_WATCHDOG_FAILURES"), 2)
            ),
            subscribe_codes=codes,
            subscribe_period=os.getenv(
                "AMAZINGDATA_SUBSCRIBE_PERIOD", "snapshot"
            ).strip(),
            force_logout=_as_bool(os.getenv("AMAZINGDATA_FORCE_LOGOUT"), False),
            idle_release_seconds=max(
                0, _as_int(os.getenv("AMAZINGDATA_IDLE_RELEASE_SECONDS"), 600)
            ),
            seat_retry_seconds=max(
                5, _as_int(os.getenv("AMAZINGDATA_SEAT_RETRY_SECONDS"), 60)
            ),
        )
