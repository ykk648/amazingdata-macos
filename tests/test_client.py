import importlib
import json
import unittest
from unittest.mock import patch

from amazingdata_macos.client import Client
from amazingdata_macos import sdk_compat
import amazingdata_macos as ad


CLIENT_MODULE = importlib.import_module("amazingdata_macos.client")


class _Response:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.data


class ClientTest(unittest.TestCase):
    def test_dynamic_namespace_returns_data(self):
        client = Client()
        response = {"ok": True, "rows": 2, "data": [20260101, 20260102]}
        with patch.object(CLIENT_MODULE, "urlopen", return_value=_Response(response)):
            self.assertEqual(
                client.base_data.get_calendar(market="SH"),
                [20260101, 20260102],
            )

    def test_raw_query_returns_envelope(self):
        client = Client()
        response = {"ok": True, "rows": 1, "data": [{"code": "510300.SH"}]}
        with patch.object(CLIENT_MODULE, "urlopen", return_value=_Response(response)):
            self.assertEqual(
                client.query("BaseData", "get_code_list", {}, raw=True),
                response,
            )

    def test_websocket_url_uses_ws_scheme_and_key(self):
        client = Client("https://localhost:8765", api_key="hello world")
        self.assertEqual(
            client.websocket_url(),
            "wss://localhost:8765/ws/market?api_key=hello%20world",
        )

    def test_sdk_compat_restores_dataframes_and_old_kline_signature(self):
        client = Client()
        response = {
            "ok": True,
            "rows": 1,
            "data": {"510300.SH": [{"kline_time": "2026-01-02", "close": 4.1}]},
        }
        with patch.object(CLIENT_MODULE, "urlopen", return_value=_Response(response)):
            result = sdk_compat.MarketData(client=client).query_kline(
                ["510300.SH"], 20260101, 20260102, sdk_compat.Period.day.value
            )
        self.assertEqual(result["510300.SH"].loc[0, "close"], 4.1)

    def test_sdk_compat_uses_gateway_cache_for_factor_and_nav(self):
        client = Client()
        with patch.object(
            client,
            "query",
            side_effect=[
                [{"code": "510300.SH", "factor": 1.0}],
                {"510300.SH": [{"date": "2026-01-02", "unit_nav": 4.0}]},
            ],
        ) as query:
            factors = sdk_compat.BaseData(client).get_backward_factor(
                ["510300.SH"], local_path="/host/cache", is_local=True
            )
            nav = sdk_compat.InfoData(client).get_fund_nav(
                ["510300.SH"], local_path="/host/cache", is_local=True
            )

        self.assertEqual(factors.loc[0, "factor"], 1.0)
        self.assertEqual(nav["510300.SH"].loc[0, "unit_nav"], 4.0)
        self.assertEqual(
            query.call_args_list[0].kwargs,
            {"args": [["510300.SH"]], "is_local": True},
        )
        self.assertEqual(
            query.call_args_list[1].kwargs,
            {"args": [["510300.SH"]], "is_local": True},
        )

    def test_package_exports_sdk_compatible_market_data(self):
        self.assertIs(ad.MarketData, sdk_compat.MarketData)


if __name__ == "__main__":
    unittest.main()
