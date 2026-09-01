import importlib
import json
import unittest
from unittest.mock import patch

from amazingdata_macos.client import Client


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


if __name__ == "__main__":
    unittest.main()
