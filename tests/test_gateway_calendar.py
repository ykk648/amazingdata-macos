"""交易日历回归测试。

背景：``BaseData.get_calendar`` 的 ``date`` 默认值在厂商 SDK 导入时求值一次，
省略 ``date`` 会把日历永久冻结在网关进程启动当天；``MarketData.query_kline``
又用 ``self.calendar`` 截断查询区间，于是 K 线会静默少几天（重启容器才恢复）。
这些用例锁定"永远显式传 date"这一约束。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import gateway.sdk as gateway_sdk
from gateway.sdk import SDKManager, trading_day_int
from gateway.settings import Settings

# 模拟"昨天启动的容器"：厂商 SDK 固化的默认 date
FROZEN_DEFAULT = 20260916
TEST_TODAY = 20260921
TRADING_DAYS = [20260914, 20260915, 20260916, 20260917, 20260918, 20260921]


def _settings(**overrides):
    base = dict(
        tgw_user="u",
        tgw_password="p",
        tgw_host="h",
        tgw_port=8600,
        api_key="",
        verify_login=True,
        local_data="/data",
        state_dir="/state",
        watchdog_interval=180,
        watchdog_failures=2,
        subscribe_codes=(),
        subscribe_period="snapshot",
    )
    base.update(overrides)
    return Settings(**base)


class _Recorder:
    def __init__(self):
        self.calendar_calls = []
        self.kline_calls = []


class _FakeBaseData:
    def __init__(self, recorder):
        self._recorder = recorder

    def get_calendar(self, *args):
        self._recorder.calendar_calls.append(args)
        # 复刻厂商 SDK：省略 date 时使用被固化在 __defaults__ 里的启动日
        date = args[2] if len(args) >= 3 else FROZEN_DEFAULT
        return [day for day in TRADING_DAYS if day <= date]


class _FakeMarketData:
    def __init__(self, calendar):
        self.calendar = calendar

    def query_kline(self, *args, **kwargs):
        return {"calendar": list(self.calendar), "kwargs": dict(kwargs)}


class _FakePeriod:
    day = "day"


class _FakeConstant:
    Period = _FakePeriod


class _FakeSDKModule:
    def __init__(self, recorder):
        self._recorder = recorder
        self.constant = _FakeConstant()

    def BaseData(self):
        return _FakeBaseData(self._recorder)

    def MarketData(self, calendar):
        return _FakeMarketData(calendar)


def _manager(recorder=None):
    recorder = recorder or _Recorder()
    manager = SDKManager(_settings())
    manager.module = _FakeSDKModule(recorder)
    manager.ready = True
    return manager, recorder


class _FrozenTradingDayTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        patcher = patch("gateway.sdk.trading_day_int", return_value=TEST_TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)


class TradingDayTests(unittest.TestCase):
    def test_trading_day_uses_china_time_not_container_utc(self):
        import datetime as dt

        # 容器是 UTC：UTC 2026-09-16 17:30 == 北京 2026-09-17 01:30
        utc_evening = dt.datetime(2026, 9, 16, 17, 30, tzinfo=dt.timezone.utc)
        china = utc_evening.astimezone(gateway_sdk.CHINA_TZ)
        # +08:00 会跨到次日，UTC 仍停在前一天——证明必须按北京时间取交易日
        self.assertEqual(int(china.strftime("%Y%m%d")), 20260917)
        self.assertEqual(int(utc_evening.strftime("%Y%m%d")), 20260916)
        self.assertEqual(gateway_sdk.CHINA_TZ.utcoffset(None).total_seconds(), 8 * 3600)
        # trading_day_int() 必须与北京时间一致
        expected = int(dt.datetime.now(gateway_sdk.CHINA_TZ).strftime("%Y%m%d"))
        self.assertEqual(trading_day_int(), expected)


class LoadCalendarTests(_FrozenTradingDayTestCase):
    def test_never_relies_on_frozen_default_date(self):
        manager, recorder = _manager()

        calendar = manager._load_calendar()

        self.assertTrue(recorder.calendar_calls)
        for call in recorder.calendar_calls:
            self.assertEqual(len(call), 3, f"必须显式传 date: {call}")
        self.assertEqual(manager._calendar_target, TEST_TODAY)
        self.assertEqual(calendar[-1], max(d for d in TRADING_DAYS if d <= TEST_TODAY))
        self.assertGreater(calendar[-1], FROZEN_DEFAULT)

    def test_extends_calendar_to_cover_requested_end_date(self):
        manager, recorder = _manager()

        manager._load_calendar(required_end=20260921)

        self.assertEqual(recorder.calendar_calls[-1], ("str", "SH", 20260921))
        self.assertEqual(manager._calendar_target, 20260921)

    def test_reuses_calendar_when_already_covered(self):
        manager, recorder = _manager()
        manager._load_calendar(required_end=20260921)
        calls = len(recorder.calendar_calls)

        manager._load_calendar()
        manager._load_calendar(required_end=20260921)

        self.assertEqual(len(recorder.calendar_calls), calls)

    def test_force_refetches_for_watchdog_probe(self):
        manager, recorder = _manager()
        manager._load_calendar()
        calls = len(recorder.calendar_calls)

        manager._load_calendar(force=True)

        self.assertEqual(len(recorder.calendar_calls), calls + 1)

    def test_force_refresh_keeps_existing_coverage(self):
        manager, recorder = _manager()
        manager._load_calendar(required_end=20260930)

        manager._load_calendar(force=True)

        self.assertEqual(recorder.calendar_calls[-1], ("str", "SH", 20260930))
        self.assertEqual(manager._calendar_target, 20260930)

    def test_empty_calendar_raises_instead_of_silently_capping(self):
        manager, _ = _manager()
        with patch.object(_FakeBaseData, "get_calendar", return_value=[]):
            with self.assertRaises(RuntimeError):
                manager._load_calendar()


class RequestedEndDateTests(unittest.TestCase):
    def test_reads_positional_end_date(self):
        self.assertEqual(
            SDKManager._requested_end_date("query_kline", ["c", 20260101, 20260930, 5], {}),
            20260930,
        )

    def test_reads_keyword_end_date(self):
        self.assertEqual(
            SDKManager._requested_end_date(
                "query_kline", [], {"end_date": 20260930, "begin_date": 20260101}
            ),
            20260930,
        )

    def test_ignores_non_kline_methods(self):
        self.assertIsNone(
            SDKManager._requested_end_date("get_backward_factor", [20260930], {})
        )

    def test_takes_max_of_end_date_and_client_calendar(self):
        self.assertEqual(
            SDKManager._requested_end_date(
                "query_kline", [], {"end_date": 20260917}, 20261031
            ),
            20261031,
        )

    def test_tolerates_garbage(self):
        self.assertIsNone(
            SDKManager._requested_end_date("query_kline", [], {"end_date": "not-a-date"})
        )


class InvokeCalendarTests(_FrozenTradingDayTestCase):
    def test_market_data_is_built_with_calendar_covering_end_date(self):
        manager, recorder = _manager()

        result = manager.invoke(
            "MarketData", "query_kline", [],
            {"code_list": ["510300.SH"], "begin_date": 20260101,
             "end_date": 20260921, "period": "day"},
        )

        self.assertEqual(manager._calendar_target, 20260921)
        self.assertEqual(result["calendar"][-1], 20260921)
        # 内部参数不能透传给厂商 SDK
        self.assertNotIn("calendar_end", result["kwargs"])

    def test_default_request_still_covers_today(self):
        manager, _ = _manager()

        result = manager.invoke(
            "MarketData", "query_kline", [],
            {"code_list": ["510300.SH"], "begin_date": 20260101, "period": "day"},
        )

        self.assertEqual(manager._calendar_target, TEST_TODAY)
        self.assertEqual(result["calendar"][-1], max(
            d for d in TRADING_DAYS if d <= TEST_TODAY))

    def test_client_supplied_calendar_end_is_honoured_and_stripped(self):
        manager, _ = _manager()

        result = manager.invoke(
            "MarketData", "query_kline", [],
            {"code_list": ["510300.SH"], "begin_date": 20260101, "end_date": 20260917,
             "period": "day", "calendar_end": 20260921},
        )

        self.assertEqual(manager._calendar_target, 20260921)
        self.assertNotIn("calendar_end", result["kwargs"])


class HealthTests(_FrozenTradingDayTestCase):
    def test_reports_calendar_coverage_and_staleness(self):
        manager, _ = _manager()
        manager._load_calendar(required_end=20260921)

        health = manager.health()

        self.assertEqual(health["trading_day"], TEST_TODAY)
        self.assertEqual(health["calendar_target"], 20260921)
        self.assertEqual(health["calendar_last_day"], 20260921)
        self.assertFalse(health["calendar_stale"])

    def test_flags_stale_calendar_when_target_is_behind_today(self):
        manager, _ = _manager()
        manager._calendar = [20260914, 20260915, 20260916]
        manager._calendar_target = 20260916

        self.assertTrue(manager.health()["calendar_stale"])


class CompatShimTests(unittest.TestCase):
    def test_market_data_forwards_calendar_end(self):
        from amazingdata_macos import sdk_compat

        client = sdk_compat.Client()
        captured = {}

        def fake_call(namespace, method, *args, **kwargs):
            captured["namespace"] = namespace
            captured["method"] = method
            captured["kwargs"] = kwargs
            return {"510300.SH": []}

        with patch.object(client, "query", side_effect=fake_call):
            sdk_compat.MarketData([20260914, 20260915, 20260921], client=client).query_kline(
                ["510300.SH"], 20260101, 20260917, sdk_compat.Period.day.value
            )

        self.assertEqual(captured["kwargs"]["calendar_end"], 20260921)

    def test_market_data_without_calendar_sends_no_calendar_end(self):
        from amazingdata_macos import sdk_compat

        client = sdk_compat.Client()
        captured = {}

        def fake_call(namespace, method, *args, **kwargs):
            captured["kwargs"] = kwargs
            return {"510300.SH": []}

        with patch.object(client, "query", side_effect=fake_call):
            sdk_compat.MarketData(client=client).query_kline(
                ["510300.SH"], 20260101, 20260917, sdk_compat.Period.day.value
            )

        self.assertNotIn("calendar_end", captured["kwargs"])

    def test_calendar_end_tolerates_unknown_payloads(self):
        from amazingdata_macos import sdk_compat

        self.assertIsNone(sdk_compat._calendar_end(None))
        self.assertIsNone(sdk_compat._calendar_end("nope"))
        self.assertEqual(sdk_compat._calendar_end([20260914, "20260921"]), 20260921)


if __name__ == "__main__":
    unittest.main()
